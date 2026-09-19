# Releasing Astro-Arc

Users only ever run releases. Everything below exists to make that true.

## The rules

- **All work happens on `development`.** Commit there, push there, test there.
- **`master` only moves by a release.** Each release is one merge commit from
  `development`, tagged `vX.Y.Z`. Nothing is ever committed to `master`
  directly, not even a one-line fix.
- **So `master` is always exactly a release.** That matters because
  `omarchy plugin update` — the only way the plugin is updated — fast-forwards
  to `master`, which is the newest tag. The plugin ships no updater of its own:
  code that fetches and checks out whatever the remote offers next is code that
  decides what runs here, and that decision stays with Omarchy.

A new install (`omarchy plugin add`, a full clone of `master`) therefore starts
on a release too.

## Cutting a release

From a clean `development` that has everything you want to ship:

```sh
bin/release --dry-run 1.0.1    # every check, every command, nothing changed
bin/release 1.0.1
```

It refuses to start unless you are on `development`, the working tree is
clean, `1.0.1` is newer than the latest release and not already tagged, local
`master` matches `origin/master`, and `development` already contains everything
on `master`. Then it runs, printing each command first:

1. set `"version": "1.0.1"` in `manifest.json` and commit it on `development`
2. `git merge --no-ff development` into `master`, so the release is one commit
3. `git tag -a v1.0.1` on that merge commit
4. fast-forward `development` to it, so its version row reads `v1.0.1`
5. `git push --atomic origin master v1.0.1`, so master and its tag arrive
   together and nobody can update into the gap between them

It does not push `development`; do that yourself when you like. For release
notes on GitHub, which the version link in the panel opens:

```sh
gh release create v1.0.1 --generate-notes
```

### Choosing the number

`MAJOR.MINOR.PATCH`: a patch for fixes, a minor for new features or settings,
a major for anything that makes a user redo their setup.

### If it stops part-way

Every step before the push is local, so nothing has reached anyone yet. The
last `+` line printed is the command that failed. Undo the steps that ran,
from the top of this table down, then run `bin/release` again:

| Undo | Only if this step ran |
| --- | --- |
| `git checkout development && git reset --hard master^2` | 4, the fast-forward (puts development back on the bump commit) |
| `git tag -d v1.0.1` | 3, the tag |
| `git checkout master && git reset --hard origin/master` | 2, the merge |
| `git checkout development && git reset --hard HEAD~1` | 1, the bump commit |

If the **push** failed, nothing was pushed (`--atomic`). Fix the cause, usually
the network or someone else moving `origin/master`, and run the printed push
line again by itself.

## Urgent fixes

There is no side door. Fix it on `development` and cut a patch release. A
release is one command, so the fast path and the correct path are the same one.

## Your own development copy

Your installed plugin is probably a symlink to your working tree
(`ln -s ~/Projects/astro-arc ~/.config/omarchy/plugins/astro-arc`), so it is
already on your branch and nothing needs to fetch anything. The version row
reads like `v1.0.1 +3 (a3f9c21)`: three commits past the release, at `a3f9c21`.
A user, sitting on a release, always sees just `v1.0.1 (a3f9c21)`.

Because that symlink makes your working tree the live plugin, the shell can
hot-reload an edited `Panel.qml`, and anything it runs on load runs for real in
your home directory. Test side effects in a throwaway `$HOME` first (see
PIPELINE.md, *Test a fresh install*), and set `XDG_DATA_HOME` there too.

## Where the version comes from

`backend/bin/astro-arc-version`: the nearest `v` tag, how far HEAD is past it,
and the short commit. One exception: `omarchy plugin update` fetches `master`
without its tags, so a user updated that way can be on `v1.0.1` without having
the tag. The release commit is the one that set `manifest.json` to `1.0.1`, so
when the manifest is newer than the nearest tag, the manifest wins. A copy that
is not a git checkout at all shows the manifest's version.
