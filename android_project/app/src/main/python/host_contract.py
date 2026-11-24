REQUIRED_BY_METHOD = {
    '_receive_open_price_pattern': ['executor', 'order_patterns', 'q', 'risk_halted', 'stop'],
    '_receive_range_reaction': ['executor', 'order_patterns', 'q', 'risk_halted', 'stop'],
    '_receive_pattern': ['executor', 'q', 'risk_halted', 'stop']
}

def required_host_attributes(source=None):
    if source is not None:
        raise ValueError("Runtime source inspection disabled in packaged Android builds")
    return {name: list(attrs) for name, attrs in REQUIRED_BY_METHOD.items()}

def check_host(host):
    required = sorted({attr for attrs in REQUIRED_BY_METHOD.values() for attr in attrs})
    missing = [a for a in required if not hasattr(host, a)]
    executor = getattr(host, "executor", None)
    if executor is not None:
        for name in ('broker', 'on_update'):
            if not hasattr(executor, name):
                missing.append("executor." + name)
    return missing

def assert_host_ready(host):
    missing = check_host(host)
    if missing:
        raise RuntimeError("PC ENGINE DISABLED: missing Android host contract: " + ", ".join(missing))
    return True
