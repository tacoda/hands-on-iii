# The Caddyfile, line by line

<!-- voice-register: informational -->
<!-- voice-english: ste -->

Chapter 16 of *Hands-on iii* replaces chapter 15's Caddyfile with `deploy/Caddyfile`. The chapter prints the file and keeps the two rules that its CSRF section depends on. This page has the rest: what each block does, the build step, and the permission Caddy needs.

## What each block does

- **`header`.** It forces HTTPS for a year, stops content-type guessing and framing, and loads every resource only from this site. On an IP site name, such as chapter 15's VM, browsers don't record `Strict-Transport-Security`, so the block can stay as it is (RFC 6797, section 8.1).
- **`basic_auth @private`.** It asks for the password on every path except `/ping`. The file needs Caddy 2.8 or later, because older releases spell the directive `basicauth`.
- **`handle /api/*`.** It answers 415 to any POST that isn't JSON, and then proxies to the `http` worker on 127.0.0.1:3111. The 64 KB body limit is a sanity check, not a security control.
- **The last `handle`.** It serves the built page. `try_files` sends a path such as `/jobs/8c41d07e` to `index.html`, so React's router can read it.

Nothing in this file reaches the console's port, 3113.

Caddy sorts directives into a fixed order, whatever their order in the file, so `basic_auth` runs before any `handle` block. `application/json*` ends in `*`, so the matcher also accepts `application/json; charset=utf-8`.

## The build

Caddy serves files, so build the page on the server. Add one step to chapter 15's upgrade steps, after the checkout and before the restart:

```shell
cd /srv/hands-on-iii/app/web && npm ci && npm run build
```

Run the build as the service user, as chapter 15 runs every `npm` command.

## The permission Caddy needs

Caddy only reads `web/dist`, but the `caddy` user needs execute permission on every directory on that path. On Ubuntu 24.04, `useradd --create-home` creates `/srv/hands-on-iii` with mode 0750, and the page answers 403. A 0700 home does the same.

If the page answers 403, give other users traverse permission on each directory above `dist`, and read permission inside it:

```shell
sudo chmod o+x /srv/hands-on-iii /srv/hands-on-iii/app /srv/hands-on-iii/app/web
sudo chmod -R o+rX /srv/hands-on-iii/app/web/dist
```

`o+x` on a directory lets a user pass through it, not list it. Caddy still serves nothing outside `web/dist`: a request for `/../config/x.yaml`, or its encoded forms, gets `index.html`.

This build takes the `chmod`, because it's two commands and the build output stays where `npm run build` puts it. The tradeoff is that every user on the server can now reach any world-readable file under `/srv/hands-on-iii` by its exact path. If that matters on your server, copy `web/dist` to a directory outside the home, such as `/var/www/hands-on-iii`, and point the Caddyfile's `root` there. Then the home can stay 0700, but each upgrade needs one more step: copy the new build after `npm run build`.

Don't add `caddy` to the `hands-on-iii` group, because that group can read the env files in `/etc/hands-on-iii`.
