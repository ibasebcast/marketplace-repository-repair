#!/usr/bin/env python3
"""Repair a Home Assistant Marketplace integration repository conflict offline."""

import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
from tempfile import NamedTemporaryFile

STORE_KEY = "marketplace.repositories"
INSTALL_FIELDS = ("installed", "installed_commit", "version_installed")
REPOSITORY_NAME = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
INTEGRATION_DOMAIN = re.compile(r"[a-z0-9_]+")
ALARM_DOT_COM_REPOS = ("pyalarmdotcom/alarmdotcom", "ibasebcast/ha-alarmdotcom")


def unique_keys(pairs):
    """Reject ambiguous input instead of silently discarding duplicate keys."""
    data = {}
    for key, value in pairs:
        if key in data:
            raise ValueError(f"Duplicate JSON key: {key}")
        data[key] = value
    return data


def prepare_repair(raw, config, old_repo, new_repo):
    """Validate both repository records and prepare a limited edit."""
    store = json.loads(raw, object_pairs_hook=unique_keys)
    if (
        not isinstance(store, dict)
        or store.get("key") != STORE_KEY
        or type(store.get("version")) is not int
        or store["version"] != 1
        or type(store.get("minor_version", 1)) is not int
        or store.get("minor_version", 1) != 1
        or not isinstance(records := store.get("data"), dict)
    ):
        raise ValueError("Unsupported Marketplace storage format. Expected version 1.1.")

    selected = {}
    names = {old_repo.casefold(), new_repo.casefold()}
    for record in records.values():
        if not isinstance(record, dict) or not isinstance(record.get("full_name"), str):
            raise ValueError("Unexpected Marketplace repository record.")
        name = record["full_name"].casefold()
        if name in names:
            if name in selected:
                raise ValueError(f"More than one record matches {name}.")
            selected[name] = record

    if selected.keys() != names:
        missing = ", ".join(sorted(names - selected.keys()))
        raise ValueError(f"Marketplace has no stored record for {missing}. Add it and refresh it first.")

    old, new = selected[old_repo.casefold()], selected[new_repo.casefold()]
    domain = old.get("domain")
    if (
        old.get("category") != "integration"
        or new.get("category") != "integration"
        or not isinstance(domain, str)
        or not INTEGRATION_DOMAIN.fullmatch(domain)
        or new.get("domain") != domain
    ):
        raise ValueError("Both Marketplace records must identify the same integration domain. Refresh the replacement first.")

    for record in records.values():
        if record.get("category") != "integration" or record.get("domain") != domain:
            continue
        if type(record.get("installed", False)) is not bool:
            raise ValueError(f"Invalid installed flag for {record['full_name']}.")
        if record.get("installed") and record is not old and record is not new:
            raise ValueError(f"Another installed repository claims {domain}: {record['full_name']}.")

    manifest_file = config / "custom_components" / domain / "manifest.json"
    if not manifest_file.is_file():
        raise ValueError(f"The installed integration manifest is missing at {manifest_file}.")
    manifest = json.loads(manifest_file.read_bytes(), object_pairs_hook=unique_keys)
    if not isinstance(manifest, dict) or manifest.get("domain") != domain:
        raise ValueError(f"The installed manifest does not confirm the {domain} domain.")

    if old.get("installed") is not True:
        return domain, None, new.get("installed", False)

    for key in INSTALL_FIELDS:
        old.pop(key, None)

    updated = json.dumps(store, indent=2, ensure_ascii=True, allow_nan=False) + "\n"
    return domain, updated.encode("utf-8"), new.get("installed", False)


def save_repair(path, original, updated):
    """Save the original bytes, then atomically replace the Marketplace file."""
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(directory, fcntl.LOCK_EX)
        return write_repair(path, original, updated)
    finally:
        os.close(directory)


def write_repair(path, original, updated):
    """Write one repair while holding the storage directory lock."""
    current = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(current.st_mode) or current.st_nlink != 1:
        raise ValueError("Marketplace storage must be a regular file with a single link.")
    if path.read_bytes() != original:
        raise ValueError("Marketplace storage changed during inspection. Run the preview again.")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    prefix = f"{path.name}.before-repository-repair-{timestamp}-"
    with NamedTemporaryFile(dir=path.parent, prefix=prefix, delete=False) as handle:
        backup = Path(handle.name)
        handle.write(original)
        handle.flush()
        os.fsync(handle.fileno())

    temp_path = None
    try:
        with NamedTemporaryFile(dir=path.parent, prefix=".marketplace-repair-", delete=False) as handle:
            temp_path = Path(handle.name)
            if (current.st_uid, current.st_gid) != (os.geteuid(), os.getegid()):
                os.fchown(handle.fileno(), current.st_uid, current.st_gid)
            os.fchmod(handle.fileno(), stat.S_IMODE(current.st_mode))
            handle.write(updated)
            handle.flush()
            os.fsync(handle.fileno())
        if path.read_bytes() != original:
            raise ValueError("Marketplace storage changed during preparation. The original was backed up.")
        os.replace(temp_path, path)
        return backup
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def read_repair(path, args):
    """Read current Marketplace storage and validate the proposed repair."""
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"Could not find a regular Marketplace storage file at {path}.")
    original = path.read_bytes()
    return original, prepare_repair(original, args.config, args.old_repo, args.new_repo)


def guided_repair(path, args, plan):
    """Use the Supervisor CLI to apply a confirmed repair while Core is stopped."""
    print("Home Assistant will go offline briefly.")
    answer = input("After creating a full backup, type REPAIR to continue: ")
    if answer.strip() != "REPAIR":
        print("Cancelled. No files were changed.")
        return 0

    try:
        subprocess.run(["ha", "core", "stop"], check=True)
        # Core can flush Marketplace storage during shutdown. Read it again
        # and check that the same repair is still appropriate before writing.
        original, current = read_repair(path, args)
        if (current[0], current[2]) != (plan[0], plan[2]) or current[1] is None:
            raise ValueError("The proposed change no longer matches the preview. Run the tool again.")
        backup = save_repair(path, original, current[1])
        print(f"Repair applied. Original Marketplace metadata saved to {backup}", flush=True)
    finally:
        try:
            subprocess.run(["ha", "core", "start"], check=True)
        except (OSError, subprocess.CalledProcessError):
            print("Automatic start failed. Run 'ha core start' to bring Home Assistant back online.",
                  file=sys.stderr)
            raise
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=Path("/config"), type=Path, help="Home Assistant configuration directory (default: /config)")
    parser.add_argument("--from", dest="old_repo", help="Blocking GitHub repository (owner/name)")
    parser.add_argument("--to", dest="new_repo", help="Replacement GitHub repository (owner/name)")
    parser.add_argument("--guided", action="store_true", help="Preview and apply with a prompt on Home Assistant OS")
    parser.add_argument("--alarmdotcom", action="store_true", help="Start the guided Alarm.com migration on Home Assistant OS")
    parser.add_argument("--apply", action="store_true", help="Write the repair after reviewing the preview")
    parser.add_argument("--core-stopped", action="store_true", help="Confirm Home Assistant Core is stopped")
    args = parser.parse_args(argv)
    if args.alarmdotcom:
        if args.old_repo is not None or args.new_repo is not None:
            parser.error("--alarmdotcom supplies both repository names")
        args.old_repo, args.new_repo = ALARM_DOT_COM_REPOS
        args.guided = True
    if args.guided and (args.apply or args.core_stopped):
        parser.error("Guided mode handles confirmation and Core shutdown")
    if not all(name and REPOSITORY_NAME.fullmatch(name) for name in (args.old_repo, args.new_repo)):
        parser.error("--from and --to require GitHub repositories in owner/name form")
    if args.old_repo.casefold() == args.new_repo.casefold():
        parser.error("--from and --to must name different repositories")
    if args.apply and not args.core_stopped:
        parser.error("Stop Home Assistant Core before applying and confirm with --core-stopped")

    path = args.config / ".storage" / STORE_KEY
    try:
        original, plan = read_repair(path, args)
        domain, updated, installed = plan
        action = "Update" if installed else "Install"
        if updated is None:
            print(f"{args.old_repo} does not claim {domain}. No change needed.")
            print(f"{action} {args.new_repo} in Marketplace if needed.")
            return 0

        print(f"Integration: {domain}")
        print(f"Clear the installed status for {args.old_repo}.")
        print(f"Then {action.lower()} {args.new_repo} in Marketplace.")
        if args.guided:
            if not sys.stdin.isatty():
                raise ValueError("Run guided mode from an interactive SSH terminal.")
            if shutil.which("ha") is None:
                raise ValueError("Guided mode requires the Home Assistant Supervisor CLI (ha).")
            return guided_repair(path, args, plan)
        if not args.apply:
            print("Preview only. No files were changed.")
            return 0
        backup = save_repair(path, original, updated)
        print(f"Repair applied. Original Marketplace metadata saved to {backup}")
        print(f"Start Core, then {action.lower()} {args.new_repo} in Marketplace.")
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Repair stopped: {error}", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("Repair interrupted.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
