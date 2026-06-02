//! Normalized market-data **tape**: a compact, append-only binary log of
//! venue-agnostic order-book snapshots and trades. The spine that the collector
//! writes and the (forthcoming) reconstruction + replay engines read.
//!
//! Frame layout, little-endian:  `[u32 body_len][body]`
//! Body: `[u8 kind][u64 ts_ms][u8 venue_len][venue][u8 sym_len][sym][payload]`
//! - kind 0 = Trade: `[u8 side][f64 px][f64 sz]`
//! - kind 1 = Book : `[u16 n_bids][(f64 px, f64 sz) * n_bids][u16 n_asks][...]`
//!
//! Hand-rolled (no serde) so the wire format is explicit and the hot path
//! allocates nothing beyond the single frame buffer. `f64` prices are fine for
//! v1; a fixed-point integer-ticks representation is a planned upgrade for the
//! reconstruction engine (avoids float compares in the book).

use std::io::{self, Read, Write};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Side {
    Buy,
    Sell,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Level {
    pub px: f64,
    pub sz: f64,
}

/// One normalized market-data event. `ts_ms` is the venue's event time.
#[derive(Debug, Clone, PartialEq)]
pub enum Event {
    Trade {
        ts_ms: u64,
        venue: String,
        symbol: String,
        side: Side,
        px: f64,
        sz: f64,
    },
    Book {
        ts_ms: u64,
        venue: String,
        symbol: String,
        bids: Vec<Level>,
        asks: Vec<Level>,
    },
}

impl Event {
    pub fn ts_ms(&self) -> u64 {
        match self {
            Event::Trade { ts_ms, .. } | Event::Book { ts_ms, .. } => *ts_ms,
        }
    }
}

// ----------------------------------------------------------------- encoding
fn put_str(b: &mut Vec<u8>, s: &str) {
    // venue/symbol are short identifiers; one length byte is plenty.
    let bytes = s.as_bytes();
    debug_assert!(bytes.len() < 256, "identifier too long for u8 length");
    b.push(bytes.len() as u8);
    b.extend_from_slice(bytes);
}

fn put_levels(b: &mut Vec<u8>, ls: &[Level]) {
    b.extend_from_slice(&(ls.len() as u16).to_le_bytes());
    for l in ls {
        b.extend_from_slice(&l.px.to_le_bytes());
        b.extend_from_slice(&l.sz.to_le_bytes());
    }
}

fn encode_body(ev: &Event) -> Vec<u8> {
    let mut b = Vec::with_capacity(64);
    match ev {
        Event::Trade { ts_ms, venue, symbol, side, px, sz } => {
            b.push(0);
            b.extend_from_slice(&ts_ms.to_le_bytes());
            put_str(&mut b, venue);
            put_str(&mut b, symbol);
            b.push(if *side == Side::Buy { 0 } else { 1 });
            b.extend_from_slice(&px.to_le_bytes());
            b.extend_from_slice(&sz.to_le_bytes());
        }
        Event::Book { ts_ms, venue, symbol, bids, asks } => {
            b.push(1);
            b.extend_from_slice(&ts_ms.to_le_bytes());
            put_str(&mut b, venue);
            put_str(&mut b, symbol);
            put_levels(&mut b, bids);
            put_levels(&mut b, asks);
        }
    }
    b
}

/// Streams [`Event`]s to any writer as length-prefixed frames.
pub struct Writer<W: Write> {
    inner: W,
}

impl<W: Write> Writer<W> {
    pub fn new(inner: W) -> Self {
        Self { inner }
    }

    pub fn write(&mut self, ev: &Event) -> io::Result<()> {
        let body = encode_body(ev);
        self.inner.write_all(&(body.len() as u32).to_le_bytes())?;
        self.inner.write_all(&body)
    }

    pub fn flush(&mut self) -> io::Result<()> {
        self.inner.flush()
    }

    pub fn into_inner(self) -> W {
        self.inner
    }
}

// ----------------------------------------------------------------- decoding
/// Bounds-checked cursor over a frame body. Every read validates length, so a
/// truncated/corrupt tape yields a clean `InvalidData` rather than a panic.
struct Cur<'a> {
    b: &'a [u8],
    i: usize,
}

impl<'a> Cur<'a> {
    fn arr<const N: usize>(&mut self) -> Result<[u8; N], String> {
        let end = self.i + N;
        let slice = self.b.get(self.i..end).ok_or("unexpected end of frame")?;
        let mut a = [0u8; N];
        a.copy_from_slice(slice);
        self.i = end;
        Ok(a)
    }
    fn u8(&mut self) -> Result<u8, String> {
        Ok(self.arr::<1>()?[0])
    }
    fn u16(&mut self) -> Result<u16, String> {
        Ok(u16::from_le_bytes(self.arr()?))
    }
    fn u64(&mut self) -> Result<u64, String> {
        Ok(u64::from_le_bytes(self.arr()?))
    }
    fn f64(&mut self) -> Result<f64, String> {
        Ok(f64::from_le_bytes(self.arr()?))
    }
    fn string(&mut self) -> Result<String, String> {
        let n = self.u8()? as usize;
        let end = self.i + n;
        let slice = self.b.get(self.i..end).ok_or("unexpected end of frame")?;
        let s = String::from_utf8(slice.to_vec()).map_err(|_| "invalid utf8")?;
        self.i = end;
        Ok(s)
    }
    fn levels(&mut self) -> Result<Vec<Level>, String> {
        let n = self.u16()? as usize;
        let mut v = Vec::with_capacity(n);
        for _ in 0..n {
            v.push(Level { px: self.f64()?, sz: self.f64()? });
        }
        Ok(v)
    }
}

fn decode_body(b: &[u8]) -> Result<Event, String> {
    let mut c = Cur { b, i: 0 };
    let kind = c.u8()?;
    let ts_ms = c.u64()?;
    let venue = c.string()?;
    let symbol = c.string()?;
    match kind {
        0 => {
            let side = if c.u8()? == 0 { Side::Buy } else { Side::Sell };
            let px = c.f64()?;
            let sz = c.f64()?;
            Ok(Event::Trade { ts_ms, venue, symbol, side, px, sz })
        }
        1 => {
            let bids = c.levels()?;
            let asks = c.levels()?;
            Ok(Event::Book { ts_ms, venue, symbol, bids, asks })
        }
        k => Err(format!("unknown event kind {k}")),
    }
}

/// Reads [`Event`]s back from a length-prefixed tape. `read` returns `Ok(None)`
/// at clean end-of-stream.
pub struct Reader<R: Read> {
    inner: R,
}

impl<R: Read> Reader<R> {
    pub fn new(inner: R) -> Self {
        Self { inner }
    }

    pub fn read(&mut self) -> io::Result<Option<Event>> {
        let mut len_buf = [0u8; 4];
        match self.inner.read_exact(&mut len_buf) {
            Ok(()) => {}
            Err(e) if e.kind() == io::ErrorKind::UnexpectedEof => return Ok(None),
            Err(e) => return Err(e),
        }
        let len = u32::from_le_bytes(len_buf) as usize;
        let mut body = vec![0u8; len];
        self.inner.read_exact(&mut body)?;
        decode_body(&body)
            .map(Some)
            .map_err(|m| io::Error::new(io::ErrorKind::InvalidData, m))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample_events() -> Vec<Event> {
        vec![
            Event::Trade {
                ts_ms: 1_717_000_000_123,
                venue: "hyperliquid".into(),
                symbol: "BTC".into(),
                side: Side::Buy,
                px: 73_521.5,
                sz: 0.0123,
            },
            Event::Book {
                ts_ms: 1_717_000_000_456,
                venue: "hyperliquid".into(),
                symbol: "ETH".into(),
                bids: vec![Level { px: 3850.1, sz: 2.0 }, Level { px: 3850.0, sz: 5.5 }],
                asks: vec![Level { px: 3850.3, sz: 1.25 }],
            },
        ]
    }

    #[test]
    fn roundtrip_preserves_events() {
        let evs = sample_events();
        let mut buf = Vec::new();
        let mut w = Writer::new(&mut buf);
        for e in &evs {
            w.write(e).unwrap();
        }
        w.flush().unwrap();

        let mut r = Reader::new(buf.as_slice());
        let mut got = Vec::new();
        while let Some(e) = r.read().unwrap() {
            got.push(e);
        }
        assert_eq!(evs, got);
    }

    #[test]
    fn clean_eof_returns_none() {
        let mut r = Reader::new([].as_slice());
        assert_eq!(r.read().unwrap(), None);
    }

    #[test]
    fn truncated_frame_is_invalid_data_not_panic() {
        // valid 4-byte length header claiming 100 bytes, but no body
        let mut bytes = (100u32).to_le_bytes().to_vec();
        bytes.extend_from_slice(&[0u8; 10]);
        let mut r = Reader::new(bytes.as_slice());
        assert!(r.read().is_err());
    }

    #[test]
    fn corrupt_kind_is_rejected() {
        // hand-build a frame with an unknown kind byte
        let mut body = vec![9u8]; // kind 9
        body.extend_from_slice(&0u64.to_le_bytes());
        body.push(0); // empty venue
        body.push(0); // empty symbol
        let mut frame = (body.len() as u32).to_le_bytes().to_vec();
        frame.extend_from_slice(&body);
        let mut r = Reader::new(frame.as_slice());
        assert!(r.read().is_err());
    }
}
