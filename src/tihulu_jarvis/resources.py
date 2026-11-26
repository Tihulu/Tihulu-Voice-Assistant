# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""Resource profiles: what the client may use, and when it lets go.

A profile is data, not a promise about the machine: how many cores local engines
may use, when an idle engine is released, whether models stay warm in the
background, and which device the owner prefers. A profile that lets go of nothing
would eat RAM and VRAM while the user is elsewhere; a profile that releases too
eagerly would make every turn start cold. Balance is the default.
"""
import os
from dataclasses import dataclass, replace

from .voice.m1 import Hold

PERFORMANCE = "performance"
BALANCE = "balance"
RESOURCE_OPTIMIZED = "resource_optimized"
CUSTOM = "custom"

PROFILE_IDS = (PERFORMANCE, BALANCE, RESOURCE_OPTIMIZED, CUSTOM)

# A local engine is released after this many idle seconds. Performance keeps a
# model warm for the whole session; resource-optimized releases it immediately.
IDLE_RELEASE = {PERFORMANCE: 0, BALANCE: 120, RESOURCE_OPTIMIZED: 15, CUSTOM: 120}

# How many cores a local engine may use, as a share of the machine's cores.
CPU_SHARE = {PERFORMANCE: 1.0, BALANCE: 0.75, RESOURCE_OPTIMIZED: 0.5, CUSTOM: 0.75}

# Which device the owner prefers for a local engine. Both of a dual-GPU machine
# are available; the choice is the owner's, never a silent default.
GPU_PREFERENCE = {PERFORMANCE: "cuda", BALANCE: "auto", RESOURCE_OPTIMIZED: "auto",
                  CUSTOM: "auto"}
GPUS = ("auto", "cuda", "vulkan", "cpu")

# Whether models stay loaded in the background between turns (costs memory).
KEEP_WARM = {PERFORMANCE: True, BALANCE: False, RESOURCE_OPTIMIZED: False, CUSTOM: False}


@dataclass(frozen=True)
class Profile:
    id: str
    cpu_share: float = 0.75
    idle_release: int = 120
    keep_warm: bool = False
    gpu: str = "auto"
    threads: int = 0

    def checked(self):
        if self.id not in PROFILE_IDS:
            raise Hold("unknown_resource_profile")
        if type(self.cpu_share) not in (int, float) or not 0.05 <= self.cpu_share <= 1.0:
            raise Hold("invalid_local_settings")
        if type(self.idle_release) is not int or not 0 <= self.idle_release <= 86400:
            raise Hold("invalid_local_settings")
        if not isinstance(self.keep_warm, bool):
            raise Hold("invalid_local_settings")
        if self.gpu not in GPUS:
            raise Hold("invalid_local_settings")
        if type(self.threads) is not int or not 0 <= self.threads <= 256:
            raise Hold("invalid_local_settings")
        return self

    def threads_for(self, cores):
        """How many threads a local engine gets, on a machine with ``cores``."""
        # Affinity discovery can fail, including on non-Linux hosts.
        count = cores if type(cores) is int and cores > 0 else 4
        if self.threads:
            return min(self.threads, 256, count)
        return min(256, max(1, int(count * self.cpu_share)))

    def releases_after(self):
        """Seconds of idleness before a local engine is released, or None."""
        return self.idle_release or None


PRESETS = {
    PERFORMANCE: Profile(PERFORMANCE, CPU_SHARE[PERFORMANCE], IDLE_RELEASE[PERFORMANCE],
                         KEEP_WARM[PERFORMANCE], GPU_PREFERENCE[PERFORMANCE]),
    BALANCE: Profile(BALANCE, CPU_SHARE[BALANCE], IDLE_RELEASE[BALANCE],
                     KEEP_WARM[BALANCE], GPU_PREFERENCE[BALANCE]),
    RESOURCE_OPTIMIZED: Profile(RESOURCE_OPTIMIZED, CPU_SHARE[RESOURCE_OPTIMIZED],
                                IDLE_RELEASE[RESOURCE_OPTIMIZED],
                                KEEP_WARM[RESOURCE_OPTIMIZED],
                                GPU_PREFERENCE[RESOURCE_OPTIMIZED]),
}


def cores():
    """The machine's core count, or None when it cannot be read."""
    try:
        return len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        return None


LABELS = {
    PERFORMANCE: "performance",
    BALANCE: "balance",
    RESOURCE_OPTIMIZED: "resource-optimized",
    CUSTOM: "custom",
}


def preset(name):
    """A ready-made profile, checked. An unknown id is refused."""
    if not isinstance(name, str) or name not in PRESETS:
        raise Hold("unknown_resource_profile")
    return PRESETS[name]


def effective(stored, cores_count=None):
    """The profile in force, from a stored id plus optional overrides.

    A custom profile is a copy of a preset with the owner's overrides applied,
    so nothing is silently inherited from a preset the owner did not choose.
    """
    if not isinstance(stored, dict):
        raise Hold("invalid_local_settings")
    name = stored.get("profile", BALANCE)
    if name not in PROFILE_IDS:
        raise Hold("unknown_resource_profile")
    base = preset(BALANCE) if name == CUSTOM else preset(name)
    unknown = set(stored) - {"profile", "cpu_share", "idle_release", "keep_warm",
                             "gpu", "threads"}
    if unknown:
        # An override this client cannot honour is refused, not ignored: a
        # silently dropped key would read as a setting that took effect.
        raise Hold("invalid_local_settings")
    if name != CUSTOM and len(stored) > 1:
        # A preset takes no overrides; the custom profile is where they live.
        raise Hold("invalid_local_settings")
    overrides = {key: stored[key] for key in
                 ("cpu_share", "idle_release", "keep_warm", "gpu", "threads")
                 if key in stored}
    return replace(base, id=name, **overrides).checked()


def environment(profile, cores_count=None):
    """The environment a local engine is started with, as a plain dict.

    Threads are bounded by the profile, and the GPU preference is exposed as a
    variable the engine reads. Nothing here starts a process.
    """
    profile = profile.checked()
    count = cores_count if cores_count is not None else cores()
    if count is None:
        count = 4
    threads = profile.threads_for(count)
    out = {"OMP_NUM_THREADS": str(threads), "TIHULU_THREADS": str(threads)}
    if profile.gpu in ("cuda", "vulkan"):
        out["TIHULU_DEVICE"] = profile.gpu
    elif profile.gpu == "cpu":
        out["TIHULU_DEVICE"] = "cpu"
    return out


def stored(profile_id, overrides=None):
    """A stored form, validated on read, as the settings module keeps it."""
    overrides = dict(overrides or {})
    if profile_id not in PROFILE_IDS:
        raise Hold("unknown_resource_profile")
    if profile_id == CUSTOM:
        unknown = set(overrides) - {"cpu_share", "idle_release", "keep_warm",
                                    "gpu", "threads"}
        if unknown:
            raise Hold("invalid_local_settings")
        replace(preset(BALANCE), **overrides).checked()
    elif overrides:
        raise Hold("invalid_local_settings")
    return {"profile": profile_id, **overrides}


def checked(stored_form):
    """A stored resource profile: a known id and possible overrides."""
    if not isinstance(stored_form, dict):
        raise Hold("invalid_local_settings")
    name = stored_form.get("profile", BALANCE)
    if name not in PROFILE_IDS:
        raise Hold("unknown_resource_profile")
    if name == CUSTOM:
        overrides = {key: value for key, value in stored_form.items()
                     if key != "profile"}
        unknown = set(overrides) - {"cpu_share", "idle_release", "keep_warm",
                                    "gpu", "threads"}
        if unknown:
            raise Hold("invalid_local_settings")
        # A frozen profile is replaced, never mutated in place.
        replace(preset(BALANCE), **overrides).checked()
    elif set(stored_form) - {"profile"}:
        raise Hold("invalid_local_settings")
    return name
