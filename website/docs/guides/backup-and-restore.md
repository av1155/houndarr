---
sidebar_position: 9
title: Backup and Restore
description: How to back up the Houndarr data directory and restore it after a rebuild.
---

# Backup and Restore

The data directory (`/data` inside the container, typically
`/mnt/user/appdata/houndarr` on Unraid or `./data` in Docker
Compose) holds everything stateful. Back it up regularly.

## What is in the data directory

| File | Contents | Sensitivity |
|------|----------|-------------|
| `houndarr.db` | SQLite database: password hash, session signing secret, encrypted *arr API keys, instance configs, search logs | High |
| `houndarr.masterkey` | Fernet key that decrypts the stored API keys | Critical |
| `houndarr.db-wal` / `houndarr.db-shm` | SQLite write-ahead log and shared memory (temporary) | Incidental |

Together, `houndarr.db` plus `houndarr.masterkey` are the entire
state. Nothing else in the container carries secrets.

## Backup guidance

- **Back up the whole directory.** The database cannot be used to
  decrypt API keys without the matching master key, and the master
  key on its own is useless. Keep them together.
- **Treat the directory as sensitive.** It carries the master key,
  the bcrypt password hash, and the session signing secret. Store
  backups with the same protections you apply to other appdata
  secrets.
- **Any archiver works.** There is no Houndarr-specific backup
  format. `tar`, `rsync`, `restic`, `duplicati`, Unraid CA Backup,
  or the backup tool you already use for appdata all work.

Example with `tar`:

```bash
tar czf houndarr-$(date +%Y%m%d).tar.gz -C /mnt/user/appdata houndarr
```

## Restoring after a container rebuild

1. Stop the Houndarr container.
2. Restore the data directory to its original path.
3. Confirm file ownership matches the runtime UID/GID
   (`chown -R 1000:1000` for generic Docker, `chown -R 99:100`
   for Unraid). See
   [Security Overview: Explicit non-root mode](/docs/security/overview#explicit-non-root-mode)
   for the explicit-non-root case.
4. Start the container.

Houndarr uses the restored database and master key as-is. Login,
instance configs, and search history pick up where they left off.

## What happens when the master key is lost

:::danger[Master key loss]

Without `houndarr.masterkey`, every stored *arr API key becomes
unrecoverable ciphertext. The file cannot be regenerated from the
database: Houndarr writes a new random key whenever the file is
missing, and the old keys only decrypt with the old one.

:::

Houndarr still starts. It writes a new `houndarr.masterkey` and logs
a warning naming that file and each instance whose API key it cannot
decrypt. Instance names, URLs, types, and settings survive; only the
API keys are lost. Those instances stop searching. An enabled one
shows as failing on the dashboard and the Settings page, with
`API key can't be decrypted with the current master key; enter it again in Settings`
on the dashboard and the Logs page. A disabled one shows the same
error once you enable it.

If you still have the original file, put it back before you re-enter
any key: stop Houndarr, restore `houndarr.masterkey`, and start it
again. Every key decrypts as before. Once you have re-entered keys,
keep the new file, since those keys are encrypted with it.

Otherwise, re-enter the key for each instance:

1. On the Settings page, click **Edit** on the instance. The dialog
   says the stored API key can't be decrypted and leaves the
   **API Key** field empty.
2. Enter the API key from your *arr instance and click
   **Test Connection**.
3. Click **Save Changes**. The instance starts searching again.

### A damaged master key file

When `houndarr.masterkey` exists but does not hold a valid key, for
example an empty or truncated file from a full disk or a bad copy,
Houndarr leaves it untouched, logs a CRITICAL line naming the file,
and exits with code 1. Restore the file from a backup, or delete it
so Houndarr writes a new one and then re-enter every API key as
above.

## Migrating between hosts

Copy the whole data directory. File ownership may need adjusting on
the destination host, which is the same guidance as the rebuild
case above.

If you are migrating between different container runtimes (for
example generic Docker to Unraid, or PUID/PGID to
`securityContext.runAsUser`), see
[Security Overview: Explicit non-root mode](/docs/security/overview#explicit-non-root-mode)
for the ownership caveats.
