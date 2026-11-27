// SPDX-License-Identifier: AGPL-3.0-only
use jarvis_store_fixture::{Id, State, Store};
use rusqlite::Connection;

fn id(label: &str) -> Id {
    match label {
        "S" => [b's'; 16],
        "T" => [b't'; 16],
        "C" => [b'c'; 16],
        "D" => [b'd'; 16],
        _ => panic!("invalid fixture identity"),
    }
}

#[test]
fn shared_transaction_trace() {
    let mut store = Store::initialize(Connection::open_in_memory().unwrap()).unwrap();
    store.session(id("S"), None).unwrap();
    store.session(id("T"), None).unwrap();
    let mut count = 0;
    for line in include_str!("../../store/parity.tsv").lines() {
        if line.starts_with('#') {
            continue;
        }
        count += 1;
        let fields: Vec<_> = line.split('\t').collect();
        assert_eq!(fields.len(), 9, "row {count}");
        let (session, client) = (id(fields[1]), id(fields[2]));
        let rev = fields[3].parse::<i64>().unwrap();
        let key = fields[4];
        let text = fields[5];
        let actual = match fields[0] {
            "submit" => store.submit(session, client, rev, key, text).map(|r| {
                let kind = if r.replay { "replay" } else { "new" };
                let state = match r.state {
                    State::Pending => "pending",
                    State::Cancelled => "cancelled",
                    State::Drained => "drained",
                    State::Completed => "completed",
                };
                format!("{kind}:{}:{state}", r.sequence)
            }),
            "complete" => store
                .complete(session, client, key, text)
                .map(|r| format!("rev:{r}")),
            "cancel" => store
                .cancel(session, client, key, rev)
                .map(|r| format!("rev:{r}")),
            "drain" => store
                .drain(session, client, key)
                .map(|r| format!("rev:{r}")),
            _ => panic!("unknown fixture operation"),
        };
        // Only expected semantic refusals normalize to reject; storage errors
        // are defects and must not be counted as successful refusals.
        let actual = match actual {
            Ok(value) => value,
            Err(jarvis_store_fixture::Error::Database | jarvis_store_fixture::Error::Schema) => {
                panic!("unexpected storage failure at row {count}")
            }
            Err(_) => "reject".to_owned(),
        };
        assert_eq!(actual, fields[6], "row {count}");
        let (revision, messages) = store.history(session).unwrap();
        assert_eq!(revision, fields[7].parse::<i64>().unwrap(), "row {count}");
        assert_eq!(
            messages.len(),
            fields[8].parse::<usize>().unwrap(),
            "row {count}"
        );
    }
    assert_eq!(count, 25);
}
