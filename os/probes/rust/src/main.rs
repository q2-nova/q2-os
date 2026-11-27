mod tls;
use std::sync::{Arc, atomic::{AtomicU32, Ordering}};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

#[repr(C)]
#[derive(Default)]
struct Result { value: f64, bits: u64, size: u32 }
#[link(name = "q2probe")]
unsafe extern "C" {
    fn q2_probe_mix(a: f64, b: f32, n: u64, out: *mut Result);
    fn q2_probe_callback(callback: extern "C" fn(f64, f32) -> f64, a: f64, b: f32) -> f64;
}
extern "C" fn callback(a: f64, b: f32) -> f64 { a + f64::from(b) }
thread_local! { static TLS: std::cell::Cell<u32> = const { std::cell::Cell::new(0) }; }

fn main() {
    if let Some(arg) = std::env::args().nth(1) {
        if arg == "--tls-client" {
            let url=std::env::args().nth(2).expect("URL");
            let ca=std::env::args().nth(3).expect("CA DER or -");
            tls::client(&url,&ca);
        } else if arg == "--time64" {
            assert!(SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs() > 2_208_988_800);
            std::fs::write("/tmp/q2-time64", b"future timestamp").unwrap();
            let modified = std::fs::metadata("/tmp/q2-time64").unwrap().modified().unwrap();
            assert!(modified.duration_since(UNIX_EPOCH).unwrap().as_secs() > 2_208_988_800);
            std::fs::remove_file("/tmp/q2-time64").unwrap();
            println!("Q2_TIME64_PASS clock and filesystem metadata after 2038");
        } else { tls::run(&arg); }
        return;
    }
    assert_eq!(std::mem::size_of::<usize>(), 4);
    assert!(f32::from_bits(0x7fc00000).is_nan());
    assert!((-0.0f64).is_sign_negative());
    let mut result = Result::default();
    unsafe {
        q2_probe_mix(1.25, 2.0, 0xfedcba9876543210, &mut result);
        assert_eq!(q2_probe_callback(callback, 1.25, 2.0), 3.25);
    }
    assert_eq!(result.value, 2.5);
    assert_eq!(result.bits, 0xfedcba9876543210 ^ 0x123456789abcdef0);
    assert_eq!(result.size as usize, std::mem::size_of::<Result>());
    let n = Arc::new(AtomicU32::new(0));
    let workers: Vec<_> = (1..=4).map(|id| {
        let n = n.clone();
        std::thread::spawn(move || {
            TLS.set(id);
            for _ in 0..1000 { n.fetch_add(1, Ordering::SeqCst); }
            assert_eq!(TLS.get(), id);
        })
    }).collect();
    for worker in workers { worker.join().unwrap(); }
    assert_eq!(n.load(Ordering::SeqCst), 4000);
    assert_eq!(TLS.get(), 0);
    let future = UNIX_EPOCH + Duration::from_secs(2_208_988_800);
    assert!(future > SystemTime::now());
    assert_eq!(future.duration_since(UNIX_EPOCH).unwrap().as_secs(), 2_208_988_800);
    std::fs::write("/tmp/q2-rust-probe", b"synthetic").unwrap();
    assert_eq!(std::fs::read("/tmp/q2-rust-probe").unwrap(), b"synthetic");
    std::fs::remove_file("/tmp/q2-rust-probe").unwrap();
    println!("Q2_RUST_PASS ffi fp64 nan2008 tls threads atomic32 time fileio");
}
