from pathlib import Path


def test_server_image_installs_pinned_russian_trusted_ca_chain_without_tls_bypass() -> None:
    dockerfile = (Path(__file__).parents[1] / "Dockerfile").read_text(encoding="utf-8")

    assert "ca-certificates" in dockerfile
    assert "russian_trusted_root_ca_pem.crt" in dockerfile
    assert "russian_trusted_sub_ca_pem.crt" in dockerfile
    assert "936a43fea6e8e525bcc0f81acd9c3d21b4fc4b9b68acea7906d698005afc6504" in dockerfile
    assert "f0ae589f36774f29ef3648f7984b08d42fcce6f1ffeeb6236d773daeb2744ea6" in dockerfile
    assert "sha256sum" in dockerfile
    assert "update-ca-certificates" in dockerfile

    lowered = dockerfile.lower()
    assert "curl -k" not in lowered
    assert "curl --insecure" not in lowered
    assert "ssl_cert_reqs=none" not in lowered
    assert "cert_none" not in lowered
