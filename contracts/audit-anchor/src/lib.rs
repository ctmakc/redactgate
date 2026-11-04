//! RedactGate audit anchor.
//!
//! RedactGate keeps a per-team HMAC hash chain of redaction events (entity *type counts*
//! only, never raw values). Anyone who holds the HMAC key — an insider, a compromised
//! admin — can rewrite that chain and recompute every hash, so the chain alone cannot
//! prove that history was never edited.
//!
//! This contract closes that gap. Every few minutes the gateway writes the current head
//! of each team's chain here: `(stream, events, head)`. Anchors are append-only and the
//! event count must strictly grow, so a later rewrite of any anchored event shows up as
//! a mismatch between the local chain and the on-chain head.
//!
//! What goes on chain: an opaque stream tag (HMAC of the team id), a 64-hex chain head
//! and an event count. No team names, no prompts, no entity values.

use near_sdk::store::{IterableSet, LookupMap};
use near_sdk::{env, log, near, require, AccountId, BorshStorageKey, PanicOnDefault};

const MAX_STREAM_LEN: usize = 64;
const MAX_NOTE_LEN: usize = 128;

#[derive(BorshStorageKey)]
#[near]
enum StorageKey {
    Writers,
    Latest,
    History,
}

/// One notarized chain head.
#[near(serializers = [borsh, json])]
#[derive(Clone, Debug, PartialEq)]
pub struct Anchor {
    /// Number of events in the chain at the time of anchoring (1-based position of `head`).
    pub events: u64,
    /// Lowercase hex HMAC-SHA256 `event_hash` of the event at position `events`.
    pub head: String,
    /// Account that wrote the anchor.
    pub writer: AccountId,
    /// Block height and timestamp (ns) at which the anchor was recorded.
    pub block_height: u64,
    pub timestamp_ns: u64,
    /// Optional short free-form label, e.g. "cron" or "export-2026-11".
    pub note: Option<String>,
}

#[near(contract_state)]
#[derive(PanicOnDefault)]
pub struct AuditAnchor {
    owner: AccountId,
    writers: IterableSet<AccountId>,
    latest: LookupMap<String, Anchor>,
    /// key: "<stream>#<events>"
    history: LookupMap<String, Anchor>,
    total_anchors: u64,
}

fn is_hex64(s: &str) -> bool {
    s.len() == 64 && s.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

fn valid_stream(s: &str) -> bool {
    !s.is_empty()
        && s.len() <= MAX_STREAM_LEN
        && s.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
}

#[near]
impl AuditAnchor {
    #[init]
    pub fn new(owner: AccountId) -> Self {
        let mut writers = IterableSet::new(StorageKey::Writers);
        writers.insert(owner.clone());
        Self {
            owner,
            writers,
            latest: LookupMap::new(StorageKey::Latest),
            history: LookupMap::new(StorageKey::History),
            total_anchors: 0,
        }
    }

    // ── admin ──────────────────────────────────────────────────────────────

    pub fn add_writer(&mut self, account_id: AccountId) {
        self.assert_owner();
        self.writers.insert(account_id);
    }

    pub fn remove_writer(&mut self, account_id: AccountId) {
        self.assert_owner();
        self.writers.remove(&account_id);
    }

    // ── write ──────────────────────────────────────────────────────────────

    /// Notarize the head of one audit stream. Append-only: `events` must be strictly
    /// greater than the last anchored count for this stream.
    pub fn anchor(&mut self, stream: String, events: u64, head: String, note: Option<String>) -> Anchor {
        let writer = env::predecessor_account_id();
        require!(self.writers.contains(&writer), "not an authorized writer");
        require!(valid_stream(&stream), "stream must be 1-64 chars of [A-Za-z0-9_-]");
        require!(is_hex64(&head), "head must be 64 lowercase hex chars");
        require!(events > 0, "events must be > 0");
        if let Some(n) = &note {
            require!(n.len() <= MAX_NOTE_LEN, "note too long");
        }
        if let Some(prev) = self.latest.get(&stream) {
            require!(events > prev.events, "events must grow (append-only)");
        }

        let a = Anchor {
            events,
            head: head.clone(),
            writer: writer.clone(),
            block_height: env::block_height(),
            timestamp_ns: env::block_timestamp(),
            note,
        };
        self.history.insert(format!("{stream}#{events}"), a.clone());
        self.latest.insert(stream.clone(), a.clone());
        self.total_anchors += 1;

        // NEP-297 event so indexers can follow anchors without reading state.
        log!(
            "EVENT_JSON:{{\"standard\":\"redactgate_audit_anchor\",\"version\":\"1.0.0\",\"event\":\"anchor\",\"data\":[{{\"stream\":\"{}\",\"events\":{},\"head\":\"{}\",\"writer\":\"{}\"}}]}}",
            stream, events, head, writer
        );
        a
    }

    // ── views ──────────────────────────────────────────────────────────────

    pub fn get_latest(&self, stream: String) -> Option<Anchor> {
        self.latest.get(&stream).cloned()
    }

    pub fn get_anchor(&self, stream: String, events: u64) -> Option<Anchor> {
        self.history.get(&format!("{stream}#{events}")).cloned()
    }

    pub fn get_owner(&self) -> AccountId {
        self.owner.clone()
    }

    pub fn is_writer(&self, account_id: AccountId) -> bool {
        self.writers.contains(&account_id)
    }

    pub fn get_writers(&self) -> Vec<AccountId> {
        self.writers.iter().cloned().collect()
    }

    pub fn total_anchors(&self) -> u64 {
        self.total_anchors
    }

    fn assert_owner(&self) {
        require!(env::predecessor_account_id() == self.owner, "owner only");
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use near_sdk::test_utils::VMContextBuilder;
    use near_sdk::testing_env;

    fn acc(s: &str) -> AccountId {
        s.parse().unwrap()
    }

    fn ctx(pred: &str, height: u64) {
        let mut b = VMContextBuilder::new();
        b.predecessor_account_id(acc(pred)).block_height(height);
        testing_env!(b.build());
    }

    const H1: &str = "1111111111111111111111111111111111111111111111111111111111111111";
    const H2: &str = "2222222222222222222222222222222222222222222222222222222222222222";

    #[test]
    fn owner_anchors_and_reads_back() {
        ctx("owner.testnet", 10);
        let mut c = AuditAnchor::new(acc("owner.testnet"));
        c.anchor("team_ab12".into(), 3, H1.into(), Some("cron".into()));
        ctx("owner.testnet", 11);
        c.anchor("team_ab12".into(), 5, H2.into(), None);
        let latest = c.get_latest("team_ab12".into()).unwrap();
        assert_eq!(latest.events, 5);
        assert_eq!(latest.head, H2);
        assert_eq!(latest.block_height, 11);
        assert_eq!(c.get_anchor("team_ab12".into(), 3).unwrap().head, H1);
        assert_eq!(c.total_anchors(), 2);
    }

    #[test]
    #[should_panic(expected = "events must grow")]
    fn rejects_rewind() {
        ctx("owner.testnet", 10);
        let mut c = AuditAnchor::new(acc("owner.testnet"));
        c.anchor("s".into(), 5, H1.into(), None);
        c.anchor("s".into(), 5, H2.into(), None);
    }

    #[test]
    #[should_panic(expected = "not an authorized writer")]
    fn rejects_stranger() {
        ctx("owner.testnet", 10);
        let mut c = AuditAnchor::new(acc("owner.testnet"));
        ctx("mallory.testnet", 11);
        c.anchor("s".into(), 1, H1.into(), None);
    }

    #[test]
    #[should_panic(expected = "head must be 64 lowercase hex")]
    fn rejects_bad_head() {
        ctx("owner.testnet", 10);
        let mut c = AuditAnchor::new(acc("owner.testnet"));
        c.anchor("s".into(), 1, "ABC".into(), None);
    }

    #[test]
    fn added_writer_can_anchor() {
        ctx("owner.testnet", 10);
        let mut c = AuditAnchor::new(acc("owner.testnet"));
        c.add_writer(acc("gw.testnet"));
        ctx("gw.testnet", 12);
        c.anchor("s".into(), 1, H1.into(), None);
        assert!(c.is_writer(acc("gw.testnet")));
        assert_eq!(c.get_latest("s".into()).unwrap().writer, acc("gw.testnet"));
    }
}
