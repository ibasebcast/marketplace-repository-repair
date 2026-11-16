# Marketplace Repository Repair

An independent workaround for [Home Assistant Core #190454](https://github.com/home-assistant/core/issues/190454). It covers integration updates blocked by a stale repository and deliberate switches between repositories that publish the same integration domain.

Marketplace can report that the integration directory belongs to another repository. Its **Delete and uninstall** button removes the configured integration and its devices and entities. Choose **Cancel** if you want to keep that setup.

The tool clears the old repository's installed status and version information in Marketplace's local metadata. It leaves the integration files, config entries, and registries in place. Marketplace then handles the replacement install or update normally. A replacement release can still change entity IDs or migrate configuration, so check its compatibility before switching.

## Requirements

- Marketplace on Home Assistant Core 2026.11, using repository storage format 1.1. The tool was developed against the 2026.11.1 source.
- The original and replacement repositories must both be visible in Marketplace. Add or refresh the replacement first so Marketplace has recorded its integration domain.

This is experimental. Automated tests use synthetic Marketplace data and simulated Core restarts. Try it on a spare instance before relying on it on a live installation.

## Repair from the Home Assistant browser

| Add the repair app repository | Open the repair app | Add Alarm.com to Marketplace |
| --- | --- | --- |
| [![Add the repair app repository to Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fibasebcast%2Fmarketplace-repository-repair) | [![Open Marketplace Repository Repair](https://my.home-assistant.io/badges/supervisor_app.svg)](https://my.home-assistant.io/redirect/supervisor_app/?app=9daa5e6c_marketplace_repair) | [![Open the maintained Alarm.com listing in Marketplace](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=ibasebcast&repository=ha-alarmdotcom&category=integration) |

1. Make a full Home Assistant backup. Use **Add Alarm.com to Marketplace** to open the maintained listing and add it if prompted. For another integration, use **Settings → Marketplace → Add from link** to add its replacement.
2. Use **Add the repair app repository** and confirm **Add**. Then use **Open the repair app** and choose **Install → Start → Open Web UI**. You can also find it under **Settings → Apps → Install app**.
3. Pick **Preview switch to maintained Alarm.com** or **Preview switch to original Alarm.com**. For another integration, expand **Repair another integration** and enter both repository names. Review the change, confirm that you have a backup, and choose **Repair and restart Home Assistant**.
4. Home Assistant may briefly disconnect. Once it is back, reopen the app to see the result, then open Marketplace and choose **Install** or **Update** on the replacement. Verify your existing devices and automations.

The app runs on Home Assistant OS (64-bit ARM and x86-64). Supervisor builds it on your device and it includes Python. You can stop or uninstall the repair app after verification.

## Guided repair from SSH on Home Assistant OS

The command-line options below require Python 3.10+ and a shell that stays available while Core stops.

1. Create a full Home Assistant backup. In **Settings → Marketplace**, add the
   replacement repository with **Add from link** if it's missing, then open its
   details so Marketplace has a record for it. For Alarm.com, add
   `ibasebcast/ha-alarmdotcom`. Cancel **Delete and uninstall** if offered.
2. Connect from your computer with an SSH client to a terminal app that stays
   available while Home Assistant Core stops. Check `python3 --version`.
   If Python is missing from the official **Terminal & SSH** app, add
   `python3` to the app's `apks` list and restart the app.
3. For Alarm.com, paste this in the SSH session:

   ```sh
   curl -fsSL https://raw.githubusercontent.com/ibasebcast/marketplace-repository-repair/main/repair.py -o /tmp/marketplace-repair.py &&
     python3 /tmp/marketplace-repair.py --alarmdotcom
   ```

   Review the plan. Type **REPAIR** to apply it. The tool stops Core, saves the
   original Marketplace metadata, clears the conflict, and starts Core again.
   Press Enter to cancel.
4. Once Home Assistant is back, open the replacement in Marketplace and use
   **Install** or **Update**, as shown in the preview. Restart if prompted.

For another integration, use
`python3 /tmp/marketplace-repair.py --guided --from old-owner/old-repo --to new-owner/new-repo`.
Use the repository named in the error for `--from` and the one you want to
install or update for `--to`. Add `--config` if your configuration directory
is somewhere other than `/config`.

## Manual repair on Container or from a host shell

1. Create a full Home Assistant backup.
2. Preview the repair. Use the repository named in the ownership error for `--from` and the repository you want for `--to`. Change `/config` if your configuration is elsewhere.

   ```sh
   python3 repair.py --config /config --from old-owner/old-repo --to new-owner/new-repo
   ```

3. Verify both repositories and the domain in the preview. Stop Home Assistant Core. On HA OS, an independent terminal with Supervisor CLI access can use `ha core stop`; on Container, stop the Home Assistant container from its host. Run the same command with the two write flags.

   ```sh
   python3 repair.py --config /config --from old-owner/old-repo --to new-owner/new-repo --apply --core-stopped
   ```

   `--core-stopped` is your confirmation. The tool cannot detect Core running in a different container or host.

4. Start Core again (`ha core start` on HA OS). In Marketplace, select the replacement and use **Install** or **Update**, as shown by the tool. Restart if Home Assistant asks. Verify your existing devices, entities, and automations.

For the Alarm.com migration, the repository arguments are `--from pyalarmdotcom/alarmdotcom --to ibasebcast/ha-alarmdotcom`.

## Roll back

Before installing or updating the replacement, you can restore the original metadata. With Core stopped, copy the contents of the specific `.storage/marketplace.repositories.before-repository-repair-…` file shown in the repair result over `.storage/marketplace.repositories`, then start Core. This restores the full Marketplace metadata snapshot, including other repositories' state at the time of that repair.

After installing or updating, use the full Home Assistant backup to also restore integration files and any configuration or registry changes made during setup. Each repair creates a separate timestamped metadata backup, including when switching back. Older backups, including the file from version 0.1.1, are kept.

If Core stays offline after the app runs, use the Home Assistant OS machine console and enter `ha core start`. From the `ha >` prompt, enter `core start`. When the UI is back, check the repair app's log before retrying.

## Development

Run the tests with `python3 -m unittest -v`. They use temporary configuration directories and a local HTTP server in place of Supervisor.
