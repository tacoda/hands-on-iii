# Backup without snapshots

Chapter 15 makes a volume snapshot the default. Use this only where you have none.

A stop ends every turn in flight, so stop only when `iii call factory::jobs` shows no job at a turn stage.

```shell
sudo systemctl stop hands-on-iii
sudo sh -c 'umask 077; mkdir -p /var/backups && tar -czf "/var/backups/hands-on-iii-$(date +%Y%m%dT%H%M%S).tgz" \
  /srv/hands-on-iii/app/data /srv/hands-on-iii/app/state /srv/hands-on-iii/.iii/worktrees \
  /srv/hands-on-iii/repos /srv/hands-on-iii/.ssh /etc/hands-on-iii /var/lib/caddy'
sudo systemctl start hands-on-iii
```

- `umask 077` keeps the archive readable by root only, because it holds the keys, the payloads, and the record.
- The name carries the time to the second, so a second backup on the same day does not overwrite the first.
- `/var/lib/caddy` holds Caddy's certificates on Debian and Ubuntu packages, so a rebuilt server does not ask Let's Encrypt again. Check the path your package uses.

Restore onto a spare machine with empty env files, verify the record as the service user, and start the factory there, as chapter 15 shows, before you trust the archive. Copy archives off the server, and delete old ones on a schedule you choose.
