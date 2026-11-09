//! Reading the game's memory, and rendering it.
//!
//! Every argument these slots receive is a pointer into the *game's* address space — a
//! `SessionParams`, an `AuthParams`, a `std::function` — and a diagnostic that dereferences one
//! unguarded is a fault on the very path the dump was added to explain. So nothing here
//! dereferences a pointer it has not first read through [`read`], and [`read`] is written so
//! that an address this process cannot see costs a log line rather than a signal.
//!
//! `/proc/self/mem` is what makes that cheap. A `pread` of an unmapped address fails instead of
//! faulting, and it holds the mapping lock for the length of the read — so a thread that unmaps
//! a page cannot land between a check and a copy, which is the one thing a maps-then-read
//! dance can never rule out. That property is why it is the path and the direct read is the
//! fallback, rather than the other way round.

use core::fmt;
use std::fs::File;
use std::ops::Range;
use std::os::unix::fs::FileExt;
use std::sync::OnceLock;

/// Bytes per row of a hex block, and half the width of the byte column.
const ROW: usize = 16;

/// The longest `std::string` a dump will copy out.
///
/// Every string these slots carry is a hostname, a version, a kind or a token. A size past
/// this is not one of them, and obeying a four-gigabyte "size" because a misaligned pointer
/// claimed it is how a diagnostic becomes a hang.
const MAX_STRING: usize = 4096;

/// The most characters libc++'s short string form can hold.
///
/// The object is 24 bytes and the last of them is the terminator, so `__min_cap` is 23 — which
/// the header names as `(sizeof(__long) - 1) / sizeof(value_type)`. A short string claiming
/// more than 22 characters was never a string, and this is the check that catches the one way
/// the layout could be read wrong: the first character of any short string whose low seven
/// bits exceed 22 (every letter, for a start) decodes as an impossible size.
const SHORT_MAX: usize = 22;

// --- reading ------------------------------------------------------------------------

/// The process's own memory as a file, opened once.
///
/// `None` when it cannot be opened at all, which is the only thing that sends [`read`] down
/// its fallback path. Cached rather than reopened per dump because the open is the expensive
/// half and it is the same file for the life of the process.
fn mem() -> Option<&'static File> {
    static MEM: OnceLock<Option<File>> = OnceLock::new();
    MEM.get_or_init(|| File::open("/proc/self/mem").ok()).as_ref()
}

/// Copy up to `out.len()` bytes from `addr`, and say how many were there.
///
/// A short count is not a failure — it means the mapping ended — so the caller renders what it
/// got and marks the rest. Zero means there was nothing readable at `addr` at all: unmapped,
/// or null, or a process that cannot see its own memory.
pub fn read(addr: usize, out: &mut [u8]) -> usize {
    if addr == 0 || out.is_empty() {
        return 0;
    }
    match mem() {
        Some(mem) => mem.read_at(out, addr as u64).unwrap_or(0),
        None => unsafe { direct(addr, out) },
    }
}

/// The fallback for a process that cannot open its own `/proc/self/mem`: a plain copy, taken
/// only once [`readable`] has said the range is mapped readable.
///
/// The check and the copy are not atomic, which is exactly why this is second. It exists
/// because a process that cannot read its own memory would otherwise have no diagnostics at
/// all, and reading memory the mapping table calls readable is what every debugger does.
unsafe fn direct(addr: usize, out: &mut [u8]) -> usize {
    let Some(span) = readable(addr) else {
        return 0;
    };
    let n = out.len().min(span.end.saturating_sub(addr));
    unsafe { core::ptr::copy_nonoverlapping(addr as *const u8, out.as_mut_ptr(), n) };
    n
}

/// The span of the mapping that contains `addr`, if it is mapped readable.
///
/// Readable and not merely present: an execute-only page faults on load, so a present mapping
/// is not enough to make the fallback safe.
fn readable(addr: usize) -> Option<Range<usize>> {
    mappings()
        .into_iter()
        .find(|m| m.start <= addr && addr < m.end && m.readable)
        .map(|m| m.start..m.end)
}

/// One word, little-endian, or `None` if it is not readable.
///
/// Separate from [`read`] because a pointer walk has to tell "could not read it" from "it is
/// zero": a null member is a value the code tests for, and a word that is not there is a
/// different finding.
pub fn word(addr: usize) -> Option<usize> {
    let mut buf = [0u8; 8];
    (read(addr, &mut buf) == 8).then(|| usize::from_le_bytes(buf))
}

// --- naming addresses ---------------------------------------------------------------

/// One line of `/proc/self/maps`.
struct Mapping {
    start: usize,
    end: usize,
    /// Where this range begins *in the file*, which is not zero for every mapping of a file.
    /// A shared object is mapped in several pieces, and a module-relative address that ignores
    /// the piece's own offset lands in the wrong place in `llvm-objdump`'s listing — right for
    /// the first mapping of a library and wrong for every one after it.
    file_offset: usize,
    readable: bool,
    /// The pathname column, absent for an anonymous mapping.
    path: Option<String>,
}

/// Every mapping this process has, in address order — which is the order the kernel prints
/// them in, and is relied on below.
///
/// Read afresh per call rather than cached: the table changes under a running game — the
/// loader maps and unmaps shared objects, the heap grows, a JIT maps code — and a stale copy
/// turns "is this readable?" into the wrong answer.
fn mappings() -> Vec<Mapping> {
    let Ok(text) = std::fs::read_to_string("/proc/self/maps") else {
        return Vec::new();
    };
    text.lines().filter_map(parse_map).collect()
}

/// `start-end perms offset dev inode [pathname]`, fields split on runs of spaces — the
/// pathname column is dropped entirely for an anonymous mapping, so the split cannot be
/// positional on a `' '`.
fn parse_map(line: &str) -> Option<Mapping> {
    let mut fields = line.split(' ').filter(|f| !f.is_empty());
    let range = fields.next()?;
    let perms = fields.next()?;
    let offset = fields.next()?;
    let (start, end) = range.split_once('-')?;
    // The dev and inode columns, which no dump needs.
    let _dev = fields.next();
    let _ino = fields.next();
    Some(Mapping {
        start: usize::from_str_radix(start, 16).ok()?,
        end: usize::from_str_radix(end, 16).ok()?,
        file_offset: usize::from_str_radix(offset, 16).ok()?,
        readable: perms.starts_with('r'),
        path: fields.next().map(str::to_string),
    })
}

/// Name an address the way a disassembler does: `libadventuresync.so+0x1a4c`.
///
/// File-relative, because that is the only form that is usable: the offset a shared object is
/// mapped at is decided at load time, while the one `llvm-objdump` prints is not, so the
/// difference between the two has to be taken out or the number is right in nobody's listing.
/// A mapping with no file — the heap, a stack, a JIT region — is named by whatever the kernel
/// calls it, and one the kernel does not name is `<anon>`.
pub fn describe(addr: usize) -> String {
    for m in mappings() {
        if m.start <= addr && addr < m.end {
            return match m.path.as_deref() {
                Some(path) => {
                    let within = m.file_offset + (addr - m.start);
                    format!("{}+0x{within:x}", base_name(path))
                }
                None => format!("<anon>+0x{:x}", addr - m.start),
            };
        }
    }
    "<unmapped>".to_string()
}

/// Where a word points, when it points anywhere: `libfoo.so+0x1a4c` for a module, `<anon>+0x8`
/// for the heap — and `None` for a value no mapping covers.
///
/// The floor is one page. Every pointer in this process's address space is above it and every
/// value below it is a count, a size or a status code, so a caller showing a word can say where
/// it points without also saying "unmapped" about the number 4.
pub fn points_at(value: usize) -> Option<String> {
    if value < 0x1000 {
        return None;
    }
    match describe(value) {
        name if name == "<unmapped>" => None,
        name => Some(name),
    }
}

/// The last path component, and without the ` (deleted)` the kernel appends to a file that has
/// been replaced under a running process — which is the state every library this stub is
/// mounted beside is in.
fn base_name(path: &str) -> &str {
    let path = path.strip_suffix(" (deleted)").unwrap_or(path);
    path.rsplit('/').next().unwrap_or(path)
}

// --- rendering ----------------------------------------------------------------------

/// Up to `max` bytes at `addr`, as a single `xx xx …  |ascii|` row and nothing else.
///
/// What [`hex`] is for a window, this is for a field: the bytes a decode was read against, on
/// the line that refused it. A refusal without them is a dead end — the difference between a
/// field at the wrong offset and a field whose *contents* are not text is visible only in the
/// bytes, and printing them inline keeps the two on one log line.
///
/// Short, deliberately: this is evidence for a single reading, not a window. [`hex`] is what a
/// caller wants when the question is what surrounds something.
pub fn preview(addr: usize, max: usize) -> String {
    let want = max.min(ROW);
    let mut row = [0u8; ROW];
    let got = read(addr, &mut row[..want]);
    if got == 0 {
        return "<unreadable>".to_string();
    }
    let (bytes, ascii) = columns(&row, got, want);
    format!("{bytes} |{ascii}|")
}

/// A hex block: one row per 16 bytes, `+0xOFFSET  xx xx …  |ascii|`, newline-terminated.
///
/// Offsets are relative to `addr` and the address itself is printed once by the caller: a row
/// that repeats a 48-bit address spends a third of its width on something the first row
/// already said. The indent and the log tag are the caller's too — this returns rows, not log
/// lines, so that the one place that knows what a dump looks like in logcat is the one place
/// that writes it.
///
/// A row that cannot be read at all says so in words rather than as a row of `??`, because a
/// row of `??` is also what a mistake in the *reader* looks like, and the two want telling
/// apart.
///
/// Runs of zero rows are one line rather than many, and the line gives the run's size in bytes.
/// A caller's frame is mostly holes, and its zeros are a finding — the object's bytes end and
/// nothing was written behind them — so they are summarised rather than dropped. Only a *whole*
/// row that read as all zeros joins a run, which is what keeps the count exact: a partial row
/// is the end of the block, and a short read is the reader's business and stays visible.
pub fn hex(addr: usize, len: usize) -> String {
    let mut out = String::new();
    let mut offset = 0;
    let mut zeros = 0;
    while offset < len {
        let want = (len - offset).min(ROW);
        let mut row = [0u8; ROW];
        let got = read(addr + offset, &mut row[..want]);
        if want == ROW && got == want && row[..want].iter().all(|byte| *byte == 0) {
            zeros += 1;
        } else {
            end_zero_run(&mut out, &mut zeros, offset);
            if got == 0 {
                out.push_str(&format!("+{offset:#06x}  <unreadable>\n"));
            } else {
                let (bytes, ascii) = columns(&row, got, want);
                out.push_str(&format!("+{offset:#06x}  {bytes} |{ascii}|\n"));
            }
        }
        offset += want;
    }
    end_zero_run(&mut out, &mut zeros, offset);
    out
}

/// Close a run of zero rows ending at `end`, where `end` is the offset of the next row.
fn end_zero_run(out: &mut String, zeros: &mut usize, end: usize) {
    if *zeros == 0 {
        return;
    }
    let bytes = *zeros * ROW;
    out.push_str(&format!("+{:#06x}  <{bytes} bytes of zeros>\n", end - bytes));
    *zeros = 0;
}

/// One row's two columns: the bytes, padded to a fixed width so the ascii column lines up
/// under itself down the block, and the ascii column.
fn columns(row: &[u8; ROW], got: usize, want: usize) -> (String, String) {
    let mut bytes = String::with_capacity(ROW * 3 + 1);
    let mut ascii = String::with_capacity(ROW);
    for i in 0..want {
        if i == ROW / 2 {
            bytes.push(' ');
        }
        if i < got {
            bytes.push_str(&format!("{:02x} ", row[i]));
            ascii.push(if row[i].is_ascii_graphic() || row[i] == b' ' {
                row[i] as char
            } else {
                '.'
            });
        } else {
            bytes.push_str("?? ");
            ascii.push('?');
        }
    }
    while bytes.len() < ROW * 3 + 1 {
        bytes.push(' ');
    }
    (bytes, ascii)
}

// --- decoding the game's own types --------------------------------------------------

/// A libc++ `std::string` as it lies in memory.
#[derive(Debug)]
pub struct CxxString {
    /// The characters, decoded as UTF-8.
    pub text: String,
    pub size: usize,
    /// The capacity the header carries, or `None` for the short form — which stores no
    /// capacity at all, because its whole capacity is the object. `Some` is therefore also
    /// what says the string is heap-backed: the two forms are told apart by this word alone.
    pub capacity: Option<usize>,
    /// Where the characters were read from, so a caller can show it beside the text.
    pub data: usize,
}

/// Which check refused a word that was read as a `std::string`.
///
/// One variant per check rather than a single "no", because the two halves want different
/// actions: a field that holds no string at all says the caller's layout table is wrong, and a
/// string whose *contents* are not text says the table is right and the value is opaque — the
/// difference between editing an offset and printing a token as hex.
#[derive(Debug)]
pub enum Reason {
    /// The 24 bytes of the object itself did not read.
    NoHeader,
    /// The size field is past what a string of this form can hold.
    TooLong { limit: usize },
    /// The long form's capacity is below its own size, which `std::string` never writes.
    CapacityBelowSize { capacity: usize },
    /// The characters are not in a readable mapping.
    DataUnreadable,
    /// `data()[size()]` is not the NUL libc++ guarantees there.
    NoTerminator,
    /// A NUL among the characters, which no `std::string` a dump is interested in has.
    InteriorNul,
    /// The characters are not UTF-8.
    NotUtf8,
}

/// Why a word that was read as a `std::string` is not one — and what was being looked at.
///
/// `data` and `size` are the candidates the header named, carried out of the decoder because a
/// caller showing the bytes it refused on is the only way a refusal becomes actionable, and
/// they do not survive anywhere else. They are left at zero when the header did not get far
/// enough to name them, or when what it named cannot be trusted: a size past every limit has a
/// pointer next to it that is not a pointer to anything.
#[derive(Debug)]
pub struct NotAString {
    pub reason: Reason,
    pub data: usize,
    pub size: usize,
}

impl fmt::Display for NotAString {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self.reason {
            Reason::NoHeader => write!(f, "the object's 24 bytes did not read"),
            Reason::TooLong { limit } => {
                write!(f, "size {} over the {limit} limit", self.size)
            }
            Reason::CapacityBelowSize { capacity } => {
                write!(f, "capacity {capacity} below the size {}", self.size)
            }
            Reason::DataUnreadable => {
                write!(f, "the {} characters at 0x{:x} did not read", self.size, self.data)
            }
            Reason::NoTerminator => write!(f, "no terminator at data[{}]", self.size),
            Reason::InteriorNul => write!(f, "a NUL among the {} characters", self.size),
            Reason::NotUtf8 => write!(f, "not UTF-8"),
        }
    }
}

/// One little-endian word out of a fixed-size slice of a header.
///
/// A function rather than a `try_into().unwrap()` at each of the three call sites: the slices
/// are all eight bytes and the conversion cannot fail, but a panic in a decoder that runs while
/// the game is backgrounding itself is a crash where a log line was wanted.
fn le_word(bytes: &[u8]) -> usize {
    let mut word = [0u8; 8];
    word.copy_from_slice(bytes);
    usize::from_le_bytes(word)
}

/// Decode the libc++ `std::string` lying at `addr`, or say which check refused it.
///
/// # The layout, and why this one
///
/// The two forms are told apart by the low bit of the first byte — the classic layout, where
/// the first byte of a short string is `size << 1 | is_long` with the characters from `+1`,
/// and a long one holds the capacity and the flag in the first word, the size at `+8` and a
/// pointer to the characters at `+16`. The other layout, `_LIBCPP_ABI_ALTERNATE_STRING_LAYOUT`,
/// puts the flag in the *last* byte, the characters of a short string from `+0` and the
/// pointer of a long one at `+0`; this NDK defines it only for Apple targets and pins
/// `_LIBCPP_ABI_VERSION 1` in its `__config_site`, which is the version whose `__short` is the
/// classical one. `notes/11` reached the same layout from the compiled binary, which writes a
/// short size as `size << 1`.
///
/// **The long form's first word is `capacity | 1`, not `capacity << 1 | 1`.** `__long` packs a
/// 63-bit `__cap_` below a one-bit `__is_long_`, and `__set_long_cap` divides by
/// `__endian_factor` as it stores — 2 on this target — so the field holds `capacity / 2` and
/// `__get_long_cap` doubles it back on the way out (the NDK's `string`, `__set_long_cap`). The
/// raw word is therefore `capacity | is_long`, so the capacity is the word with the low bit
/// cleared. Two fields in the 2026-09-23 device dumps pin it: `a1` with size 156, and `31` with
/// size 31. Those are 160 and 48 under this rule and 80 and 24 under `word >> 1` — both below
/// the size in the same header, which no `std::string` has.
///
/// The flag and the long form's field order are one choice, not two: they are the two arms of
/// the header's `#ifdef`, so a decoder that took the flag from here and the order from there
/// would read every real string as a small number.
///
/// # Why a wrong answer is refused rather than rendered
///
/// A decoder that guesses at a pointer prints a plausible-looking string, and a plausible
/// string is worse than none: it reads as evidence. Every field is therefore checked against
/// something independent — the size against the form's own maximum, the capacity against the
/// size, the characters against the mapping, the terminator libc++ guarantees at
/// `data()[size()]`, and the encoding — and anything that fails is refused, with the reason and
/// the bytes it was read from left for the caller to print.
pub fn string(addr: usize) -> Result<CxxString, NotAString> {
    let mut head = [0u8; 24];
    if read(addr, &mut head) < head.len() {
        return Err(NotAString {
            reason: Reason::NoHeader,
            data: 0,
            size: 0,
        });
    }
    let long = head[0] & 1 != 0;
    let (data, size, capacity) = if long {
        (
            le_word(&head[16..24]),
            le_word(&head[8..16]),
            Some(le_word(&head[0..8]) & !1),
        )
    } else {
        // `size << 1 | is_long` in the first byte and the characters from `+1`. What follows
        // them is whatever the longest string that ever used this object left in its padding.
        (addr + 1, (head[0] >> 1) as usize, None)
    };
    let limit = if long { MAX_STRING } else { SHORT_MAX };
    if size > limit {
        return Err(NotAString {
            reason: Reason::TooLong { limit },
            // The pointer beside a size that cannot be one is not a pointer to anything, so
            // naming it here would only send the caller's preview into an unmapped page.
            data: 0,
            size,
        });
    }
    if let Some(capacity) = capacity {
        if capacity < size {
            return Err(NotAString {
                reason: Reason::CapacityBelowSize { capacity },
                data: 0,
                size,
            });
        }
    }
    let mut text = vec![0u8; size];
    // From here on the header parsed, so `data` and `size` are worth showing: these are the
    // refusals where the field is probably a string and its *contents* are the finding.
    if read(data, &mut text) < size {
        return Err(NotAString {
            reason: Reason::DataUnreadable,
            data,
            size,
        });
    }
    // `data()[size()]` is NUL in every `std::string` libc++ builds. A byte anything else there
    // means the pointer and the size did not come from a string object.
    let mut terminator = [1u8; 1];
    if read(data + size, &mut terminator) < 1 || terminator[0] != 0 {
        return Err(NotAString {
            reason: Reason::NoTerminator,
            data,
            size,
        });
    }
    if text.contains(&0) {
        return Err(NotAString {
            reason: Reason::InteriorNul,
            data,
            size,
        });
    }
    let Ok(text) = String::from_utf8(text) else {
        return Err(NotAString {
            reason: Reason::NotUtf8,
            data,
            size,
        });
    };
    Ok(CxxString {
        text,
        size,
        capacity,
        data,
    })
}

/// The two words of a libc++ `shared_ptr`: the object and its control block.
pub struct SharedPtr {
    pub ptr: usize,
    pub ctrl: usize,
}

/// Read a `shared_ptr` from `addr` — as a local, or as the `shared_ptr const&` a caller passes
/// a pointer to, which is the same two words either way.
///
/// `None` when the pair is not readable, which is *not* the same as a pair of nulls: an empty
/// `shared_ptr` is a value, and telling one from the other is most of what these dumps are for.
pub fn shared_ptr(addr: usize) -> Option<SharedPtr> {
    let ptr = word(addr)?;
    let ctrl = word(addr + 8)?;
    Some(SharedPtr { ptr, ctrl })
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The middle of the widest hole in this process's address space.
    ///
    /// The middle, and the widest, so that a mapping created between reading the table and
    /// reading the address is as far from where the read lands as the process allows.
    fn a_gap() -> usize {
        let maps = mappings();
        assert!(maps.len() > 2, "a live process has mappings");
        let mut widest = (0usize, 0usize);
        for pair in maps.windows(2) {
            let size = pair[1].start.saturating_sub(pair[0].end);
            if size > widest.0 {
                widest = (size, pair[0].end + size / 2);
            }
        }
        // Every process on Linux has one: the binary, the libraries and the heap sit far below
        // the stack with nothing in between.
        assert!(widest.0 >= 0x2000, "the address space has a hole to read");
        widest.1
    }

    /// The offset out of a `describe` string, so two of them can be compared.
    fn offset_of(desc: &str) -> usize {
        let tail = desc.rsplit_once("+0x").expect("a named address").1;
        usize::from_str_radix(tail, 16).expect("a hex offset")
    }

    #[test]
    fn read_returns_the_bytes_that_are_there() {
        let data: [u8; 8] = [1, 2, 3, 4, 5, 6, 7, 8];
        let mut out = [0u8; 8];
        assert_eq!(read(data.as_ptr() as usize, &mut out), 8);
        assert_eq!(out, data);
    }

    #[test]
    fn a_word_reads_little_endian() {
        let pair: [usize; 2] = [0x0123_4567_89ab_cdef, 7];
        let at = pair.as_ptr() as usize;
        assert_eq!(word(at), Some(0x0123_4567_89ab_cdef));
        assert_eq!(word(at + 8), Some(7), "and the next word is read from the next 8 bytes");
    }

    #[test]
    fn a_gap_reads_as_nothing_rather_than_faulting() {
        let gap = a_gap();
        let mut out = [0xAAu8; 32];
        assert_eq!(read(gap, &mut out), 0, "nothing is mapped at {gap:#x}");
        assert_eq!(out, [0xAAu8; 32], "a failed read leaves the buffer alone");
        assert_eq!(describe(gap), "<unmapped>");
        assert_eq!(hex(gap, 16), "+0x0000  <unreadable>\n");
        assert!(string(gap).is_err());
        assert!(shared_ptr(gap).is_none());
        assert_eq!(word(gap), None);
        assert_eq!(preview(gap, 16), "<unreadable>");
        assert_eq!(points_at(gap), None);
    }

    #[test]
    fn preview_shows_the_bytes_inline_and_says_when_there_are_none() {
        let data = b"background";
        let line = preview(data.as_ptr() as usize, 10);
        assert!(line.starts_with("62 61 63 6b 67 72 6f 75  6e 64"), "{line}");
        assert!(line.ends_with("|background|"), "{line}");
        assert_eq!(line.lines().count(), 1, "one row, so a field's bytes fit on its line");
    }

    #[test]
    fn points_at_names_a_mapping_and_refuses_to_name_a_number() {
        let data = [0u8; 8];
        let here = data.as_ptr() as usize;
        let name = points_at(here).expect("a local is mapped");
        assert_ne!(name, "<unmapped>");
        // The floor: a status code, a size or a count that a word happens to hold is not a
        // pointer, and `<unmapped>` beside the number 4 would be worse than saying nothing.
        assert_eq!(points_at(0), None);
        assert_eq!(points_at(4), None);
        assert_eq!(points_at(a_gap()), None);
    }

    #[test]
    fn hex_lays_out_sixteen_bytes_a_row_with_an_ascii_column() {
        let data = b"background token";
        assert_eq!(
            hex(data.as_ptr() as usize, 16),
            "+0x0000  62 61 63 6b 67 72 6f 75  6e 64 20 74 6f 6b 65 6e  |background token|\n"
        );
    }

    #[test]
    fn hex_numbers_each_row_from_the_base_address() {
        let data = [0x41u8; 40];
        let block = hex(data.as_ptr() as usize, 40);
        let offsets: Vec<&str> = block.lines().map(|row| &row[..7]).collect();
        assert_eq!(offsets, ["+0x0000", "+0x0010", "+0x0020"]);
    }

    /// A run of zero rows is one line, whose byte count says how far the run reaches.
    ///
    /// The count rather than the offsets, because the offsets around it are already on the rows
    /// above and below and a byte count is what cannot be miscounted. A run reaches the end of
    /// the block in the second case, and a row that only partly read is never part of one — the
    /// rest of that row is not there, so its bytes of zeros are not either.
    #[test]
    fn a_run_of_zero_rows_is_one_line_with_its_size() {
        let mut data = [0u8; 0x50];
        data[0] = 0xab;
        data[0x40] = 0xcd;
        let block = hex(data.as_ptr() as usize, data.len());
        let lines: Vec<&str> = block.lines().collect();
        assert_eq!(
            lines,
            [
                "+0x0000  ab 00 00 00 00 00 00 00  00 00 00 00 00 00 00 00  |................|",
                "+0x0010  <48 bytes of zeros>",
                "+0x0040  cd 00 00 00 00 00 00 00  00 00 00 00 00 00 00 00  |................|",
            ],
            "the run between the two bytes, and nothing else collapsed"
        );
        let tail = [0u8; 0x18];
        assert_eq!(
            hex(tail.as_ptr() as usize, 0x18),
            "+0x0000  <16 bytes of zeros>\n\
             +0x0010  00 00 00 00 00 00 00 00                           |........|\n",
            "the partial row is its own row: half a row of zeros is not a row of zeros"
        );
    }

    #[test]
    fn a_row_shorter_than_sixteen_bytes_shows_only_what_was_asked_for() {
        let data = [0x41u8; 16];
        let block = hex(data.as_ptr() as usize, 4);
        assert_eq!(block.lines().count(), 1);
        assert!(block.ends_with("|AAAA|\n"), "{block}");
    }

    #[test]
    fn a_non_printable_byte_shows_as_a_dot() {
        let data = [0x00u8, 0x1b, b'a', 0xff];
        let block = hex(data.as_ptr() as usize, 4);
        assert!(block.ends_with("|..a.|\n"), "{block}");
    }

    #[test]
    fn describe_names_the_mapping_a_local_object_lives_in() {
        let data = [0u8; 16];
        let name = describe(data.as_ptr() as usize);
        assert_ne!(name, "<unmapped>", "a local is mapped somewhere");
        assert!(name.contains("+0x"), "{name}");
    }

    #[test]
    fn describe_measures_every_offset_from_the_mappings_own_start() {
        // The offsets are checked against a mapping's own edges rather than against a local
        // object eight bytes on: an object can end exactly at a page boundary, and an address
        // eight bytes past it is then in the *next* mapping, whose offset is measured from a
        // different place. `describe` consults the mapping table and reads nothing, so an
        // address inside a mapping does not have to be one this process can dereference.
        //
        // The mapping is one whose file offset is not zero, which is the case that tells "file
        // offset plus distance" from "distance" apart — a shared object is mapped in pieces
        // and only the first of them starts at the beginning of the file.
        let m = mappings()
            .into_iter()
            .find(|m| m.file_offset != 0 && m.path.is_some() && m.end - m.start >= 0x2000)
            .expect("a file mapped past its first byte");
        let name = base_name(m.path.as_deref().unwrap());
        let span = m.end - m.start;
        assert_eq!(describe(m.start), format!("{name}+0x{:x}", m.file_offset));
        assert_eq!(describe(m.start + 0x10), format!("{name}+0x{:x}", m.file_offset + 0x10));
        assert_eq!(
            describe(m.end - 1),
            format!("{name}+0x{:x}", m.file_offset + span - 1),
            "the last byte of the mapping, so the whole span is inside it"
        );
        assert_eq!(offset_of(&describe(m.start + 8)) - offset_of(&describe(m.start)), 8);
    }

    /// A libc++ string in its short form, as `__set_short_size` writes it: the size and the
    /// flag in the first byte, the characters from `+1`, the terminator after them.
    fn short_string(text: &str) -> [u8; 24] {
        let mut buf = [0u8; 24];
        assert!(text.len() <= SHORT_MAX);
        buf[0] = (text.len() as u8) << 1;
        buf[1..1 + text.len()].copy_from_slice(text.as_bytes());
        buf
    }

    /// A libc++ string in its long form — the capacity with the flag in its low bit, the size at
    /// `+8`, the characters at `+16` — with the characters kept alive by the returned vector so
    /// the pointer in the header stays valid for the test's lifetime.
    fn long_string(text: &str) -> ([u8; 24], Vec<u8>) {
        let heap = text.as_bytes().iter().copied().chain([0]).collect::<Vec<u8>>();
        let mut buf = [0u8; 24];
        // `__cap_ * __endian_factor | __is_long_`, which for an even capacity is `capacity | 1`
        // — so the first byte alone says "long" and the word is the capacity plus that bit.
        buf[0..8].copy_from_slice(&((heap.capacity() | 1) as u64).to_le_bytes());
        buf[8..16].copy_from_slice(&text.len().to_le_bytes());
        buf[16..24].copy_from_slice(&(heap.as_ptr() as usize).to_le_bytes());
        (buf, heap)
    }

    #[test]
    fn a_short_string_decodes_from_its_own_bytes() {
        let buf = short_string("background");
        let decoded = string(buf.as_ptr() as usize).expect("decodes");
        assert_eq!(decoded.text, "background");
        assert_eq!(decoded.capacity, None, "the short form stores no capacity");
        assert_eq!(decoded.size, 10);
        assert_eq!(decoded.data, buf.as_ptr() as usize + 1);
    }

    #[test]
    fn an_empty_short_string_decodes_as_empty() {
        let buf = short_string("");
        let decoded = string(buf.as_ptr() as usize).expect("decodes");
        assert_eq!(decoded.text, "");
        assert_eq!(decoded.capacity, None);
    }

    #[test]
    fn a_long_string_decodes_through_its_pointer() {
        let (buf, heap) = long_string("devholoholo.nianticlabs.com/plfe");
        let decoded = string(buf.as_ptr() as usize).expect("decodes");
        assert_eq!(decoded.text, "devholoholo.nianticlabs.com/plfe");
        assert_eq!(decoded.data, heap.as_ptr() as usize);
        assert!(
            decoded.capacity.is_some_and(|cap| cap >= decoded.size),
            "a long form carries a capacity, and never one below its size: {:?}",
            decoded.capacity
        );
    }

    /// The rule the device pinned, stated as a header rather than as a fixture, because the
    /// fixture's capacity is the vector's and this test is about which bits mean what.
    ///
    /// `a1 00 00 00 00 00 00 00` with a size of 156 is a real header out of the 2026-09-23
    /// dumps. As `capacity | __is_long_` it is a capacity of 160; as `capacity << 1 | flag` —
    /// what this decoder's doc claimed before the dumps — it would be 80, below the size in the
    /// same header, which is a `std::string` that cannot exist.
    #[test]
    fn a_long_forms_capacity_is_the_first_word_with_its_flag_bit_cleared() {
        let heap = vec![b'x'; 156].into_iter().chain([0u8]).collect::<Vec<u8>>();
        let mut buf = [0u8; 24];
        buf[0..8].copy_from_slice(&0xa1u64.to_le_bytes());
        buf[8..16].copy_from_slice(&156u64.to_le_bytes());
        buf[16..24].copy_from_slice(&(heap.as_ptr() as usize as u64).to_le_bytes());

        let decoded = string(buf.as_ptr() as usize).expect("the device's own header decodes");
        assert_eq!(decoded.size, 156);
        assert_eq!(decoded.capacity, Some(160), "`a1` is 160 with the flag bit cleared");
    }

    #[test]
    fn a_long_form_whose_capacity_is_below_its_size_is_refused() {
        // The check that only exists once the capacity is read: a capacity below the size is a
        // header no `std::string` writes, so a decoder that reaches one has read the wrong
        // words. Under the wrong rule this header would pass silently — its capacity would be
        // 16, and nothing would compare it to anything.
        let heap = vec![b'x'; 64].into_iter().chain([0u8]).collect::<Vec<u8>>();
        let mut buf = [0u8; 24];
        buf[0..8].copy_from_slice(&((32u64) | 1).to_le_bytes());
        buf[8..16].copy_from_slice(&64u64.to_le_bytes());
        buf[16..24].copy_from_slice(&(heap.as_ptr() as usize as u64).to_le_bytes());

        let refused = string(buf.as_ptr() as usize).expect_err("capacity 32, size 64");
        assert_eq!(
            refused.to_string(),
            "capacity 32 below the size 64",
            "the refusal names the check rather than saying \"not a string\""
        );
    }

    #[test]
    fn a_short_form_claiming_more_than_it_can_hold_is_refused() {
        // One past the maximum, with the flag clear — which is also what the first character
        // of any short string whose low seven bits are that large would decode as, and the
        // reason a wrong layout guess fails loudly instead of printing a plausible string.
        let mut buf = [b'x'; 24];
        buf[0] = ((SHORT_MAX as u8) + 1) << 1;
        let refused = string(buf.as_ptr() as usize).expect_err("one past the short maximum");
        assert!(refused.to_string().contains("over the 22 limit"), "{refused}");
    }

    #[test]
    fn a_string_with_an_interior_nul_is_refused() {
        let buf = short_string("ab\0cd");
        let refused = string(buf.as_ptr() as usize).expect_err("a NUL inside");
        assert!(refused.to_string().contains("NUL among"), "{refused}");
    }

    #[test]
    fn bytes_that_never_were_a_string_are_refused() {
        let garbage = [0xffu8; 24];
        assert!(string(garbage.as_ptr() as usize).is_err());
        // A long form whose pointer and size are fine but whose `data[size]` is not the NUL
        // libc++ guarantees — the check that separates a string object from a structure that
        // merely has a pointer and a small number where a string would have them.
        let heap = [b'a', b'b', b'X'];
        let mut buf = [0u8; 24];
        buf[0..8].copy_from_slice(&(((heap.as_ptr() as usize) << 1) | 1).to_le_bytes());
        buf[8..16].copy_from_slice(&2usize.to_le_bytes());
        buf[16..24].copy_from_slice(&(heap.as_ptr() as usize).to_le_bytes());
        let refused = string(buf.as_ptr() as usize).expect_err("data[2] is not a terminator");
        assert_eq!(refused.size, 2);
        assert_eq!(refused.data, heap.as_ptr() as usize, "the candidates survive the refusal");
    }

    #[test]
    fn a_pointer_pair_reads_as_two_words() {
        let pair: [usize; 2] = [0x1234, 0x5678];
        let read_back = shared_ptr(pair.as_ptr() as usize).expect("reads");
        assert_eq!((read_back.ptr, read_back.ctrl), (0x1234, 0x5678));
    }

    #[test]
    fn an_empty_pair_is_a_value_and_is_not_confused_with_an_unreadable_one() {
        let empty: [usize; 2] = [0, 0];
        let read_back = shared_ptr(empty.as_ptr() as usize).expect("reads");
        assert_eq!((read_back.ptr, read_back.ctrl), (0, 0));
        assert!(shared_ptr(a_gap()).is_none());
    }
}
