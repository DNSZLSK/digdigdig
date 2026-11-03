"""`ddd selftest` / `DDD.exe --selftest` : le harnais lui-meme (rapport, code retour).

Les vrais checks (binaires sldl/fpcalc, reseau) ne tournent pas ici : les binaires sont
gitignored (absents en CI) et le reseau n'est pas deterministe. On prouve que le harnais
rapporte fidelement : un echec reste un echec, un SKIP n'en est pas un.
"""

from ddd.core import selftest


def _ok(tmp):
    return "fine"


def _skip(tmp):
    return "SKIP (no token)"


def _boom(tmp):
    raise RuntimeError("kaput")


def test_report_and_exit_code(monkeypatch, tmp_path):
    monkeypatch.setattr(selftest, "OFFLINE", [("a", _ok), ("b", _skip)])
    monkeypatch.setattr(selftest, "NETWORK", [("c", _boom)])
    report = tmp_path / "r.txt"
    assert selftest.run(report=report) == 1
    text = report.read_text(encoding="utf-8")
    assert "[OK  ] a" in text and "[SKIP] b" in text and "[FAIL] c" in text
    assert "kaput" in text and "2/3 OK - 1 ECHEC(S)" in text


def test_offline_skips_network_and_passes(monkeypatch, tmp_path):
    monkeypatch.setattr(selftest, "OFFLINE", [("a", _ok)])
    monkeypatch.setattr(selftest, "NETWORK", [("c", _boom)])
    assert selftest.main(["--selftest", "--offline", "--report", str(tmp_path / "r.txt")]) == 0
    assert "1/1 OK" in (tmp_path / "r.txt").read_text(encoding="utf-8")


def test_audio_fixture_checks_run_from_source(tmp_path):
    # chaines de decodage reelles (sans binaire externe) : WAV/FLAC/MP4/ALAC/WMA -> verdict
    for fn in (selftest._c_wav, selftest._c_flac, selftest._c_mp4_aac,
               selftest._c_m4a_alac, selftest._c_wma, selftest._c_scan_mp4_folder):
        assert fn(tmp_path)
