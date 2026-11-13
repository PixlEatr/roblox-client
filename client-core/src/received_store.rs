//! received records live here, behind a bounded c api.
//! the compact blobs keep bytes, tuples, non-finite floats and missing fields intact.
//! big records get compressed losslessly, and a read unpacks one record at a time.
//! the host gets a small working cache, not another copy of the entire world.
use std::{collections::{BTreeMap, HashMap}, panic::{catch_unwind, AssertUnwindSafe}, sync::Mutex};

const MAX_VALUE: usize = 32 * 1024 * 1024;
const MAX_ROWS: usize = 100_000;
const MAX_DEPTH: usize = 64;

// Shared with scripts/rust_world_store.py. This is a local typed codec, not
// the Roblox network format and not an executable object/pickle encoding.
const NONE: u8 = 0;
const FALSE: u8 = 1;
const TRUE: u8 = 2;
const INT: u8 = 3;
const FLOAT: u8 = 4;
const BYTES: u8 = 5;
const STRING: u8 = 6;
const LIST: u8 = 7;
const TUPLE: u8 = 8;
const MAP: u8 = 9;

fn var(out: &mut Vec<u8>, mut value: u64) {
    while value >= 128 { out.push((value as u8 & 127) | 128); value >>= 7; }
    out.push(value as u8);
}
fn integer(out: &mut Vec<u8>, value: u64) {
    out.push(INT); out.push(0); var(out, value);
}
fn text(out: &mut Vec<u8>, value: &str) {
    out.push(STRING); var(out, value.len() as u64); out.extend_from_slice(value.as_bytes());
}
fn sequence(out: &mut Vec<u8>, count: usize) { out.push(LIST); var(out, count as u64); }

#[derive(Clone, Copy)]
struct Reader<'a> { data: &'a [u8], at: usize }
impl<'a> Reader<'a> {
    fn take(&mut self, n: usize) -> Result<&'a [u8], &'static str> {
        let end=self.at.checked_add(n).filter(|n| *n<=self.data.len()).ok_or("truncated store value")?;
        let result=&self.data[self.at..end]; self.at=end; Ok(result)
    }
    fn byte(&mut self) -> Result<u8, &'static str> { Ok(self.take(1)?[0]) }
    fn var(&mut self) -> Result<u64, &'static str> {
        let mut value=0u64;
        for index in 0..10 {
            let b=self.byte()?;
            if index==9 && b>1 { return Err("store integer overflow"); }
            value|=u64::from(b & 127)<<(index*7);
            if b & 128==0 { return Ok(value); }
        }
        Err("store integer overflow")
    }
    fn count(&mut self) -> Result<usize, &'static str> {
        usize::try_from(self.var()?).ok().filter(|n| *n<=MAX_VALUE).ok_or("store length exceeds limit")
    }
    fn value(&mut self, depth: usize) -> Result<&'a [u8], &'static str> {
        if depth>MAX_DEPTH { return Err("store nesting exceeds limit"); }
        let start=self.at;
        match self.byte()? {
            NONE|FALSE|TRUE => (),
            INT => { if self.byte()?>1 {return Err("invalid integer sign");} self.var()?; },
            FLOAT => {self.take(8)?;},
            BYTES|STRING => {
                let string=self.data[start]==STRING; let n=self.count()?; let data=self.take(n)?;
                if string && std::str::from_utf8(data).is_err() {return Err("invalid store utf8");}
            },
            LIST|TUPLE|MAP => {
                let map=self.data[start]==MAP; let n=self.count()?;
                let values=n.checked_mul(if map {2}else{1}).ok_or("store count overflow")?;
                if values>self.data.len()-self.at {return Err("truncated store collection");}
                for _ in 0..values {self.value(depth+1)?;}
            },
            _ => return Err("unknown store value type"),
        }
        Ok(&self.data[start..self.at])
    }
    fn finish(&self) -> Result<(), &'static str> {
        if self.at==self.data.len() {Ok(())} else {Err("trailing store bytes")}
    }
}

fn validate(data: &[u8]) -> Result<(), &'static str> {
    if data.len()>MAX_VALUE {return Err("store record exceeds limit");}
    let mut r=Reader{data,at:0}; r.value(0)?; r.finish()
}
fn field<'a>(data: &'a [u8], name: &str) -> Result<Option<&'a [u8]>, &'static str> {
    let mut r=Reader{data,at:0};
    if r.byte()?!=MAP {return Ok(None);}
    for _ in 0..r.count()? {
        let key=r.value(0)?; let value=r.value(0)?;
        let mut k=Reader{data:key,at:0};
        if k.byte()?==STRING { let n=k.count()?; if k.take(n)?==name.as_bytes() {return Ok(Some(value));} }
    }
    Ok(None)
}
fn class_id(data: &[u8]) -> Result<Option<u64>, &'static str> {
    let Some(value)=field(data,"class")? else {return Ok(None)};
    let mut r=Reader{data:value,at:0};
    if r.byte()?!=INT || r.byte()?!=0 {return Err("invalid stored instance class");}
    Ok(Some(r.var()?))
}

struct Row {
    bytes: Box<[u8]>, expanded: usize, compressed: bool, class: Option<u64>, ordinal: usize,
}
impl Row {
    fn new(data: &[u8], instance: bool, ordinal: usize) -> Result<Self,&'static str> {
        validate(data)?;
        let class=if instance {class_id(data)?} else {None};
        let packed=if data.len()>=512 {
            Some(zstd::bulk::compress(data,1).map_err(|_|"store compression failed")?)
        } else {None};
        let (bytes,compressed)=match packed {
            Some(packed) if packed.len()+32<data.len() => (packed.into_boxed_slice(),true),
            _ => (data.into(),false),
        };
        Ok(Self{bytes,expanded:data.len(),compressed,class,ordinal})
    }
    fn read(&self) -> Result<Vec<u8>,&'static str> {
        if self.compressed {
            let data=zstd::bulk::decompress(&self.bytes,self.expanded).map_err(|_|"store decompression failed")?;
            if data.len()!=self.expanded {return Err("store length mismatch");}
            Ok(data)
        } else {Ok(self.bytes.to_vec())}
    }
}
#[derive(Default)]
struct Space { rows: HashMap<Vec<u8>,Row>, order: BTreeMap<usize,Vec<u8>>, next_ordinal: usize }
#[derive(Default)]
pub struct ReceivedStore { spaces: [Space;4] }
impl ReceivedStore {
    fn execute(&mut self, request: &[u8]) -> Result<Option<Vec<u8>>,&'static str> {
        let mut r=Reader{data:request,at:0}; let op=r.byte()?; let kind=usize::from(r.byte()?);
        if kind>=self.spaces.len() {return Err("unknown store namespace");}
        if op==0 {
            r.finish()?;
            let mut out=vec![MAP]; var(&mut out,4);
            for (name, value) in [
                ("rows",self.spaces.iter().map(|s|s.rows.len() as u64).sum()),
                ("stored_payload_bytes",self.spaces.iter().flat_map(|s|s.rows.values()).map(|v|v.bytes.len() as u64).sum()),
                ("expanded_payload_bytes",self.spaces.iter().flat_map(|s|s.rows.values()).map(|v|v.expanded as u64).sum()),
                ("compressed_rows",self.spaces.iter().flat_map(|s|s.rows.values()).filter(|v|v.compressed).count() as u64)] {
                text(&mut out,name);integer(&mut out,value);
            }
            return Ok(Some(out));
        }
        let space=&mut self.spaces[kind];
        match op {
            1..=3|7 => {
                let key=r.value(0)?.to_vec();
                if key.len()>1024 {return Err("store key exceeds limit");}
                if op==1 {
                    let data=r.value(0)?;r.finish()?;
                    let existing=space.rows.get(&key);
                    if existing.is_none() && space.rows.len()>=MAX_ROWS {return Err("store row limit exceeded");}
                    let ordinal=existing.map(|v|v.ordinal).unwrap_or(space.next_ordinal);
                    let row=Row::new(data,kind==0,ordinal)?;
                    if existing.is_none() {
                        let next=space.next_ordinal.checked_add(1).ok_or("store ordinal overflow")?;
                        space.order.insert(ordinal,key.clone()); space.next_ordinal=next;
                    }
                    space.rows.insert(key,row);Ok(Some(vec![NONE]))
                } else {
                    r.finish()?;
                    if op==7 {return Ok(Some(vec![if space.rows.contains_key(&key){TRUE}else{FALSE}]));}
                    if op==2 {return space.rows.get(&key).map(Row::read).transpose();}
                    if let Some(row)=space.rows.remove(&key) {
                        space.order.remove(&row.ordinal);Ok(Some(row.read()?))
                    } else {Ok(None)}
                }
            },
            4|9 => {r.finish()?;let mut out=vec![];integer(&mut out,if op==4{space.rows.len()}else{space.next_ordinal} as u64);Ok(Some(out))},
            5 => {
                let start=usize::try_from(r.var()?).map_err(|_|"invalid store cursor")?;
                let stop=usize::try_from(r.var()?).map_err(|_|"invalid store cursor")?.min(space.next_ordinal);
                let limit=r.count()?;r.finish()?;
                if limit==0||limit>1024||start>stop {return Err("invalid store page");}
                let rows:Vec<_>=space.order.range(start..stop).take(limit).collect();
                let at=if rows.len()<limit {stop} else {rows.last().map(|(i,_)|**i+1).unwrap_or(stop)};
                let keys:Vec<_>=rows.iter().map(|(_,key)|*key).collect();
                let mut out=vec![TUPLE,2];integer(&mut out,at as u64);sequence(&mut out,keys.len());
                for key in keys {out.extend_from_slice(key);}
                Ok(Some(out))
            },
            6 => {
                if kind!=0 {return Err("class query requires instance namespace");}
                let count=r.count()?;if count>4096{return Err("class query exceeds limit");}
                let mut classes=Vec::with_capacity(count);for _ in 0..count {classes.push(r.var()?);}r.finish()?;
                let keys:Vec<_>=space.order.values().filter(|key|
                    space.rows.get(*key).and_then(|row|row.class).is_some_and(|id|classes.contains(&id))).collect();
                let mut out=vec![];sequence(&mut out,keys.len());for key in keys {out.extend_from_slice(key);}Ok(Some(out))
            },
            8 => {r.finish()?;*space=Space::default();Ok(Some(vec![NONE]))},
            _ => Err("unknown store operation"),
        }
    }
}

#[repr(C)]
pub struct StoreBuffer { pub data: *mut u8, pub len: usize, pub capacity: usize }
fn output(out: &mut StoreBuffer, data: Vec<u8>) {
    let mut data=std::mem::ManuallyDrop::new(data);
    *out=StoreBuffer{data:data.as_mut_ptr(),len:data.len(),capacity:data.capacity()};
}
#[no_mangle]
pub extern "C" fn rove_world_store_abi() -> u32 {1}
#[no_mangle]
pub extern "C" fn rove_world_store_new() -> *mut Mutex<ReceivedStore> {
    Box::into_raw(Box::new(Mutex::new(ReceivedStore::default())))
}
/// The caller owns this opaque handle and must not use it after free.
#[no_mangle]
pub unsafe extern "C" fn rove_world_store_free(store: *mut Mutex<ReceivedStore>) {
    if !store.is_null() {drop(Box::from_raw(store));}
}
/// Status 0 = value, 1 = missing key, 2 = sanitized error. Output is owned by
/// Rust until released through rove_world_store_buffer_free, even on error.
#[no_mangle]
pub unsafe extern "C" fn rove_world_store_call(store: *mut Mutex<ReceivedStore>,
    data: *const u8, len: usize, out: *mut StoreBuffer) -> i32 {
    if store.is_null()||out.is_null()||data.is_null()||len>MAX_VALUE+2048 {return 2;}
    *out=StoreBuffer{data:std::ptr::null_mut(),len:0,capacity:0};
    let result=catch_unwind(AssertUnwindSafe(|| {
        let input=std::slice::from_raw_parts(data,len);
        (*store).lock().map_err(|_|"world store lock failed")?.execute(input)
    }));
    match result {
        Ok(Ok(Some(value)))=>{output(&mut *out,value);0},
        Ok(Ok(None))=>1,
        Ok(Err(error))=>{output(&mut *out,error.as_bytes().to_vec());2},
        Err(_)=>{output(&mut *out,b"world store internal failure".to_vec());2},
    }
}
#[no_mangle]
pub unsafe extern "C" fn rove_world_store_buffer_free(buffer: StoreBuffer) {
    if !buffer.data.is_null() {drop(Vec::from_raw_parts(buffer.data,buffer.len,buffer.capacity));}
}

#[cfg(test)]
mod tests {
    use super::*;
    fn put(store:&mut ReceivedStore,key:&[u8],value:&[u8])->Result<Option<Vec<u8>>,&'static str> {
        let mut request=vec![1,0];request.extend_from_slice(key);request.extend_from_slice(value);store.execute(&request)
    }
    #[test]
    fn compressed_data_roundtrips_and_rejected_write_keeps_old_value() {
        let mut store=ReceivedStore::default();let key=vec![INT,0,1];
        let mut value=vec![MAP,1];text(&mut value,"payload");value.push(BYTES);var(&mut value,10000);value.extend(vec![42;10000]);
        put(&mut store,&key,&value).unwrap();
        assert!(store.spaces[0].rows[&key].bytes.len()<1000);
        assert!(put(&mut store,&key,&[MAP,1,NONE]).is_err());
        let mut request=vec![2,0];request.extend_from_slice(&key);
        assert_eq!(store.execute(&request).unwrap(),Some(value));
    }
    #[test]
    fn deletion_and_recreation_preserve_insertion_order() {
        let mut store=ReceivedStore::default();
        for n in 1..4 {put(&mut store,&[INT,0,n],&[MAP,0]).unwrap();}
        store.execute(&[3,0,INT,0,2]).unwrap();put(&mut store,&[INT,0,2],&[MAP,0]).unwrap();
        let page=store.execute(&[5,0,0,4,10]).unwrap().unwrap();
        assert_eq!(page,vec![TUPLE,2,INT,0,4,LIST,3,INT,0,1,INT,0,3,INT,0,2]);
    }
    #[test]
    fn exact_numeric_and_binary_encodings_and_limits() {
        for value in [vec![NONE],vec![TRUE],vec![FALSE],vec![BYTES,2,0,255],vec![TUPLE,2,NONE,NONE]] {validate(&value).unwrap();}
        let mut nan=vec![FLOAT];nan.extend_from_slice(&f64::NAN.to_bits().to_le_bytes());validate(&nan).unwrap();
        assert!(validate(&[STRING,1,255]).is_err());assert!(validate(&[INT,0,255,255,255,255,255,255,255,255,255,2]).is_err());
        assert!(validate(&[MAP,0,0]).is_err());
    }
}
