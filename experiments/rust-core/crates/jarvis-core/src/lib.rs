// SPDX-License-Identifier: AGPL-3.0-only
#![forbid(unsafe_code)]
//! Non-shipping, deterministic core fixtures. No OS, network or model effects.

pub mod acp_framing;
pub mod acp_v1;
pub mod approval;
pub mod approval_broker;
pub mod budget;
pub mod chat;

pub mod deadline;
pub mod harness;
pub mod harness_wave;
pub mod session;
pub mod synthetic_transport;
pub mod workspace;
