// SPDX-License-Identifier: AGPL-3.0-only
use jarvis_core::acp_framing::{Decoder, Error, MAX_CHUNK, MAX_LINE, MAX_LINES};

#[test]
fn every_byte_split_preserves_utf8_and_line_order() {
    let bytes = "{\"text\":\"Türkçe\"}\n{\"id\":1}\n".as_bytes();
    for split in 0..=bytes.len() {
        let mut d = Decoder::default();
        let mut lines = d.push(&bytes[..split]).unwrap();
        lines.extend(d.push(&bytes[split..]).unwrap());
        assert_eq!(lines, ["{\"text\":\"Türkçe\"}", "{\"id\":1}"]);
        d.finish().unwrap();
        assert!(d.closed());
    }
}

#[test]
fn invalid_suffix_discards_complete_prefix_and_closes() {
    let mut d = Decoder::default();
    assert_eq!(d.push(b"{}\n\x00"), Err(Error::Control));
    assert!(d.closed());
    assert_eq!(d.push(b"{}\n"), Err(Error::Closed));
}

#[test]
fn overlong_chunk_and_line_are_rejected() {
    let mut d = Decoder::default();
    assert_eq!(d.push(&vec![b'x'; MAX_CHUNK + 1]), Err(Error::Chunk));
    let mut d = Decoder::default();
    assert!(d.push(&vec![b'x'; MAX_LINE]).unwrap().is_empty());
    assert_eq!(d.push(b"x"), Err(Error::Line));
    let mut d = Decoder::default();
    d.push(&vec![b'x'; MAX_LINE]).unwrap();
    assert_eq!(d.push(b"\n").unwrap()[0].len(), MAX_LINE);
}

#[test]
fn per_chunk_line_count_is_bounded() {
    let mut d = Decoder::default();
    assert_eq!(d.push(&b"{}\n".repeat(MAX_LINES + 1)), Err(Error::Count));
    let mut d = Decoder::default();
    assert_eq!(d.push(&b"{}\n".repeat(MAX_LINES)).unwrap().len(), MAX_LINES);
    assert_eq!(d.push(b"{}\n").unwrap(), ["{}"]);
}

#[test]
fn rejects_blank_invalid_utf8_and_raw_controls() {
    for (bytes, error) in [
        (&b"\n"[..], Error::Empty),
        (&b" \t\n"[..], Error::Empty),
        (&b"\xff\n"[..], Error::Utf8),
        (&b"{}\r\n"[..], Error::Control),
        (&b"{}\x7f\n"[..], Error::Control),
    ] {
        let mut d = Decoder::default();
        assert_eq!(d.push(bytes), Err(error));
        assert!(d.closed());
    }
}

#[test]
fn truncated_eof_does_not_become_a_message_or_reopen() {
    let mut d = Decoder::default();
    assert!(d.push(b"{\"id\":2}").unwrap().is_empty());
    assert_eq!(d.finish(), Err(Error::Partial));
    assert_eq!(d.push(b"\n"), Err(Error::Closed));
    assert_eq!(d.finish(), Err(Error::Closed));
}

#[test]
fn independent_connections_never_share_partial_bytes() {
    let mut a = Decoder::default();
    let mut b = Decoder::default();
    a.push(b"{\"id\":").unwrap();
    assert_eq!(b.push(b"{}\n").unwrap(), ["{}"]);
    assert_eq!(a.push(b"1}\n").unwrap(), ["{\"id\":1}"]);
}
