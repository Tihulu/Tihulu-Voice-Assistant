# R1 preview gate — preliminary maintainer review

Scope: R1 stack through #86 at b0a6464ceb6a38e3a4af2db770b2f9bf8b7fa181 and the Debian runtime repair in this branch. This is a maintainer review, not independent security approval or physical acceptance.

## Findings and status

| ID | Finding | Disposition |
|---|---|---|
| PKG-01 | The previous Debian build moved a build-machine venv after pip generated absolute interpreter shebangs. The wrapper executed the relocated console script. Native system GI bindings were not guaranteed visible. | Replaced with pure Python modules and the target /usr/bin/python3 isolated launcher. Actual extracted wrapper + native GTK texture check added; CI evidence pending. |
| PKG-02 | Installed-wheel test output was piped to tail, which could mask a failed process when pipefail is not active. Package checks omitted R1 module names. | Removed the pipeline; require R1 modules and execute installed suites. CI evidence pending. |
| SEC-01 | The existing broad profile gateway and four-mode migration are not fully implemented. R1 has its own LOCAL_ONLY guard and explicit picker grant, but generic legacy permission behavior is outside this guard. | HOLD for independent review and R3 policy work; do not enable hybrid/shell capability. |
| SEC-02 | R1 worker authorization checks in-memory grants. Out-of-band profile edits are checked again at UI publication, not continuously in the worker. Loopback Jan transport does not establish that the server cannot forward requests. | Disclosed limitations; no general sandbox or guaranteed offline claim. Verify actual Jan settings and use synthetic input only until reviewed. |
| IO-01 | Export writes are bounded and no-replace, but synchronous filesystem operations can stall GTK; directory and mount namespace trust remain prerequisites. | Documented limitation; avoid slow/remote mounts in preview. |
| NATIVE-01 | Previously reported COSMIC picker crash remains unreproduced on target machine. Xvfb exercises widget logic only. | HOLD; collect local diagnostic evidence, not raw private logs. |

No additional features should be piled onto R1 until the actual installed-runtime check is green and the owner acceptance protocol has been exercised. An independent reviewer should inspect the exact final stack head, including these findings and negative tests, before any release-ready verdict.

## Safe private preview without installing over the stable app

Use a fresh worktree of the reviewed exact commit, build its wheel and Debian archive per RELEASE.md, and extract the archive to a new directory. Do not run dpkg -i yet. Run the extracted launcher with --check-runtime first. Do not start a second Jarvis using the same application ID while the stable Jarvis is running: close it normally before preview; no shared service is stopped by these instructions.

From the preview worktree, use a disposable XDG state directory and the original desktop session (do not replace the real session bus when investigating Wayland portals):

```sh
preview=$(mktemp -d)
mkdir -p "$preview/config" "$preview/data" "$preview/cache" "$preview/state"
printf 'time_s,temperature_c\n0,20\n10,22\n20,24\n30,26\n40,28\n' > "$preview/temperature.csv"
printf 'Synthetic CSV and preview state: %s\n' "$preview"
XDG_CONFIG_HOME="$preview/config" XDG_DATA_HOME="$preview/data" \
XDG_CACHE_HOME="$preview/cache" XDG_STATE_HOME="$preview/state" \
.release-work/preview-root/usr/bin/tihulu-jarvis
```

The fresh state does not inherit model settings; explicitly select existing local models, do not download or alter them. The environment changes only for the preview process. Shared services, desktop session, HOME and runtime directory are not replaced. A disposable XDG directory is not a filesystem sandbox. Keep exports and diagnostic data local. After closing the preview, the stable application can be launched normally. Do not delete or copy any stable state. Record actual results using the protocol in R1_ARTIFACT_EXPORT.md; report only sanitized reasons and the exact commit. A real clean-machine install/uninstall and rollback must still be tested separately.
