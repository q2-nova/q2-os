use std::{io::{Read, Write}, net::TcpListener, sync::Arc};
use rustls::{pki_types::{CertificateDer, PrivateKeyDer, PrivatePkcs8KeyDer}, RootCertStore};

pub fn run(dir: &str) {
    let cert = CertificateDer::from(std::fs::read(format!("{dir}/server.der")).unwrap());
    let ca = CertificateDer::from(std::fs::read(format!("{dir}/ca.der")).unwrap());
    let key = PrivateKeyDer::Pkcs8(PrivatePkcs8KeyDer::from(std::fs::read(format!("{dir}/key.der")).unwrap()));
    let config = rustls::ServerConfig::builder().with_no_client_auth().with_single_cert(vec![cert], key).unwrap();
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    let server = std::thread::spawn(move || {
        for (index, stream) in listener.incoming().take(3).enumerate() {
            let conn = rustls::ServerConnection::new(Arc::new(config.clone())).unwrap();
            let socket = stream.unwrap();
            socket.set_read_timeout(Some(std::time::Duration::from_secs(10))).unwrap();
            let mut tls = rustls::StreamOwned::new(conn, socket);
            let mut request = [0u8; 4096];
            let result = tls.read(&mut request);
            if index == 0 {
                assert!(result.unwrap() > 0);
                tls.write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 11\r\nConnection: close\r\n\r\nQ2_TLS_PASS").unwrap();
                tls.flush().unwrap();
            } else {
                // The client must abort certificate validation on negative cases.
                assert!(result.is_err());
            }
        }
    });
    let mut roots = RootCertStore::empty();
    roots.add(ca).unwrap();
    let client = rustls::ClientConfig::builder().with_root_certificates(roots).with_no_client_auth();
    let agent = ureq::AgentBuilder::new().timeout(std::time::Duration::from_secs(15)).tls_config(Arc::new(client)).build();
    assert_eq!(agent.get(&format!("https://127.0.0.1:{port}/")).call().unwrap().into_string().unwrap(), "Q2_TLS_PASS");
    // Same server, but the certificate intentionally has no DNS localhost SAN.
    assert!(agent.get(&format!("https://localhost:{port}/")).call().is_err());
    // The application's default public roots must not trust our test CA.
    assert!(ureq::get(&format!("https://127.0.0.1:{port}/")).timeout(std::time::Duration::from_secs(15)).call().is_err());
    server.join().unwrap();
    println!("Q2_TLS_PASS trusted fixture; rejected wrong hostname and untrusted CA");
}

/// External-to-guest transport probe; certificate/date checks remain enabled.
pub fn client(url: &str, ca: &str) {
    let mut builder=ureq::AgentBuilder::new().timeout(std::time::Duration::from_secs(15));
    if ca != "-" {
        let mut roots=RootCertStore::empty();
        roots.add(CertificateDer::from(std::fs::read(ca).unwrap())).unwrap();
        builder=builder.tls_config(Arc::new(rustls::ClientConfig::builder().with_root_certificates(roots).with_no_client_auth()));
    }
    match builder.build().get(url).call() {
        Ok(_) => println!("Q2_EXTERNAL_TLS_PASS"),
        Err(e) => { eprintln!("Q2_EXTERNAL_TLS_REJECT {e}"); std::process::exit(1); }
    }
}
