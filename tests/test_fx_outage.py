from automation_control.scan_ingest import fx


def reset(monkeypatch):
    monkeypatch.setattr(fx, '_cache', {})
    monkeypatch.setattr(fx, '_fetched_at', 0)
    monkeypatch.setattr(fx, '_retry_after', 0)
    monkeypatch.setattr(fx, '_is_live', False)


def test_outage_retries_once_then_recovers(monkeypatch):
    reset(monkeypatch)
    now = [100.0]
    calls = []
    monkeypatch.setattr(fx.time, 'monotonic', lambda: now[0])
    def fetch():
        calls.append(1)
        if len(calls) == 1:
            raise TimeoutError('offline')
        return {'AUD': 1.0, 'USD': 1.4, 'EUR': 1.6}
    monkeypatch.setattr(fx, '_fetch_rates', fetch)
    for _ in range(100):
        assert fx.to_aud(10, 'USD') == 15
    assert len(calls) == 1
    assert not fx.is_live()
    now[0] += fx.FAILURE_RETRY_SECONDS + 1
    assert fx.to_aud(10, 'USD') == 14
    assert len(calls) == 2
    assert fx.is_live()


def test_stale_rates_survive_outage(monkeypatch):
    reset(monkeypatch)
    monkeypatch.setattr(fx, '_cache', {'AUD': 1.0, 'USD': 1.4})
    calls = []
    def fetch():
        calls.append(1)
        raise TimeoutError('offline')
    monkeypatch.setattr(fx, '_fetch_rates', fetch)
    for _ in range(100):
        assert fx.to_aud(10, 'USD') == 14
    assert len(calls) == 1
    assert fx.rates(force=True)['USD'] == 1.4
    assert len(calls) == 2


def test_aud_needs_no_network(monkeypatch):
    reset(monkeypatch)
    def fetch():
        raise AssertionError('AUD must not fetch rates')
    monkeypatch.setattr(fx, '_fetch_rates', fetch)
    assert fx.to_aud(145, 'AUD') == 145
    assert fx.to_aud(None, 'USD') is None
