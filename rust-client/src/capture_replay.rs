//! Stream private capture rows into the Rust decoder without a Python loader
//! or a full capture JSON tree. Never executes scripts or sends captured data.
use base64::{engine::general_purpose::STANDARD, Engine};
use serde::{
    de::{self, DeserializeSeed, SeqAccess, Visitor},
    Deserialize,
};
use std::{fmt, io::Read};

#[derive(Deserialize)]
struct Row {
    direction: Option<String>,
    application: Option<u8>,
    channel: Option<u32>,
    data_base64: Option<String>,
}

struct Capture<'a, F> {
    accept: &'a mut F,
    count: usize,
    kinds: &'a [u8],
}
impl<'de, F: FnMut(&[u8], usize) -> Result<(), String>> Visitor<'de> for Capture<'_, F> {
    type Value = usize;
    fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
        f.write_str("capture row array")
    }
    fn visit_seq<A: SeqAccess<'de>>(mut self, mut seq: A) -> Result<usize, A::Error> {
        while let Some(row) = seq.next_element::<Row>()? {
            if row.direction.as_deref() != Some("received")
                || row.application != Some(4)
                || row.channel != Some(1)
            {
                continue;
            }
            let encoded = row
                .data_base64
                .ok_or_else(|| de::Error::custom("received row has no payload"))?;
            if encoded.len() > 24 * 1024 * 1024 {
                return Err(de::Error::custom("capture payload exceeds limit"));
            }
            let payload = STANDARD
                .decode(encoded)
                .map_err(|_| de::Error::custom("invalid capture payload encoding"))?;
            if payload.len() > 16 * 1024 * 1024 {
                return Err(de::Error::custom("capture packet exceeds limit"));
            }
            if !payload
                .first()
                .is_some_and(|kind| self.kinds.contains(kind))
            {
                continue;
            }
            self.count += 1;
            (self.accept)(&payload, self.count).map_err(de::Error::custom)?;
        }
        Ok(self.count)
    }
}
impl<'de, F: FnMut(&[u8], usize) -> Result<(), String>> DeserializeSeed<'de> for Capture<'_, F> {
    type Value = usize;
    fn deserialize<D: serde::Deserializer<'de>>(self, de: D) -> Result<usize, D::Error> {
        de.deserialize_seq(self)
    }
}

pub fn read<R: Read>(
    input: R,
    mut accept: impl FnMut(&[u8], usize) -> Result<(), String>,
) -> Result<usize, String> {
    read_selected(input, &mut accept, &[129, 131, 151])
}

/// Inspection only. Captured checks must never supply replies in a new session.
pub fn read_program_checks<R: Read>(
    input: R,
    mut accept: impl FnMut(&[u8], usize) -> Result<(), String>,
) -> Result<usize, String> {
    read_selected(input, &mut accept, &[155])
}

fn read_selected<R: Read>(
    input: R,
    accept: &mut impl FnMut(&[u8], usize) -> Result<(), String>,
    kinds: &[u8],
) -> Result<usize, String> {
    let mut de = serde_json::Deserializer::from_reader(input);
    let count = Capture {
        accept,
        count: 0,
        kinds,
    }
    .deserialize(&mut de)
    .map_err(|e| e.to_string())?;
    de.end()
        .map_err(|_| "trailing capture JSON data".to_string())?;
    Ok(count)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    #[test]
    fn selects_only_received_world_packets_in_order() {
        let row = |direction, app, channel, data: &[u8]| json!({"direction":direction,"application":app,"channel":channel,"data_base64":STANDARD.encode(data)});
        let capture = json!([
            row("sent", 4, 1, &[151]),
            row("received", 4, 3, &[131]),
            row("received", 4, 1, &[147]),
            row("received", 4, 1, &[151, 1]),
            row("received", 4, 1, &[129, 2]),
            row("received", 4, 1, &[131, 3])
        ]);
        let mut accepted = Vec::new();
        assert_eq!(
            read(capture.to_string().as_bytes(), |p, n| {
                accepted.push((n, p.to_vec()));
                Ok(())
            })
            .unwrap(),
            3
        );
        assert_eq!(
            accepted,
            vec![(1, vec![151, 1]), (2, vec![129, 2]), (3, vec![131, 3])]
        );
    }
    #[test]
    fn rejects_bad_json_bad_encoding_and_consumer_failure() {
        for source in [
            "[",
            "[]null",
            r#"[{"direction":"received","application":4,"channel":1,"data_base64":"bad!"}]"#,
        ] {
            assert!(read(source.as_bytes(), |_, _| Ok(())).is_err());
        }
        let source=json!([{"direction":"received","application":4,"channel":1,"data_base64":STANDARD.encode([131])}]).to_string();
        assert!(
            read(source.as_bytes(), |_, _| Err("consumer rejected".into()))
                .unwrap_err()
                .contains("consumer rejected")
        );
    }
}
