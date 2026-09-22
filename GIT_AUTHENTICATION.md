# GitHub Authentication From WSL

Use this procedure when Git runs in WSL/Linux but the existing GitHub sign-in is held by Windows Git Credential Manager. It uses the Windows credential only for the command being run; it does not print credentials or write a credential helper into this repository's configuration.

## Windows Git Credential Manager

The installed executable is:

```text
C:\Program Files\Git\mingw64\bin\git-credential-manager.exe
```

From WSL, use its mounted path and quote it inside a one-command Git configuration:

```sh
git -c 'credential.helper=!"/mnt/c/Program Files/Git/mingw64/bin/git-credential-manager.exe"' \
  push origin HEAD:desktop-app
```

The `-c` setting exists only for that Git invocation. Do not run `git config credential.helper ...` for this procedure, and do not use `git credential fill` in a way that prints its output.

## Verify The Remote Branch

Query the remote ref with the same temporary helper configuration:

```sh
git -c 'credential.helper=!"/mnt/c/Program Files/Git/mingw64/bin/git-credential-manager.exe"' \
  ls-remote --heads origin refs/heads/desktop-app
```

Compare the returned SHA with the commit intended for publication, for example:

```sh
git rev-parse HEAD
```

## Troubleshooting

- If the shell reports `/mnt/c/Program: not found`, the helper path was split at its spaces. Keep the complete `credential.helper=!...` value in single quotes and the executable path in double quotes exactly as shown.
- WSL needs the Linux-mounted form `/mnt/c/Program Files/...`, not the Windows `C:\Program Files\...` path. To convert a different Windows path, run `wslpath -u 'C:\Path\To\file.exe'`.
- If the executable is absent, reinstall or repair Git for Windows, then authenticate through its normal Git Credential Manager sign-in flow. Never copy, paste, log, or commit a token.
