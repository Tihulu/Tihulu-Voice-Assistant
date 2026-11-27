// SPDX-License-Identifier: AGPL-3.0-only
#![forbid(unsafe_code)]
//! Non-shipping, deterministic core fixtures. No OS, network or model effects.

pub mod budget;
pub mod chat;

pub mod deadline;
pub mod harness;
pub mod harness_wave;
pub mod session;
pub mod workspace;
