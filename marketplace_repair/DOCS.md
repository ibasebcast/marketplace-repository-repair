# Marketplace Repository Repair

Use this app on Home Assistant OS when Marketplace says the integration folder belongs to a different GitHub repository. Choose **Cancel** if Marketplace offers to delete and uninstall your existing integration.

1. Create a full Home Assistant backup.
2. [Open the maintained Alarm.com listing in Marketplace](https://my.home-assistant.io/redirect/marketplace_repository/?owner=ibasebcast&repository=ha-alarmdotcom&category=integration) and add it if prompted. For other integrations, use **Settings → Marketplace → Add from link**. Open the listing so Marketplace records its integration domain.
3. Start this app, choose **Open Web UI**, and pick the version you want to switch to. For another integration, enter the repository named in the error and the repository you want to install.
4. Review the preview. Check the backup box and choose **Repair and restart Home Assistant**.
5. When Home Assistant reconnects, reopen this app to see the result. Then go to Marketplace and select **Install** or **Update** on the replacement.

The app writes only Marketplace's repository metadata. It requests that Supervisor stop Core before the write and start it afterward. The browser may briefly disconnect while Core is down.

## Recovery

Each repair saves a separate timestamped snapshot of Marketplace metadata in `.storage`. The repair result shows its filename. To undo the repair, restore the full backup from **Settings → System → Backups**. It also restores integration files and any configuration changes made by the replacement after installation.

If Core is still offline after several minutes, see the [app repository](https://github.com/ibasebcast/marketplace-repository-repair) for the manual recovery commands.

This app is experimental and uses an internal Marketplace storage format. It checks the format before applying a repair.
