"""Operator-supplied Bybit TradFi USDT perpetual watchlist (not an exchange guarantee)."""
TRADFI_SYMBOLS='UMCUSDT BMNUUSDT VKTXUSDT MELIUSDT PBRUSDT NUSTOCKUSDT YPFUSDT VISTUSDT ZCSHUSDT SNPSUSDT GRALUSDT PGUSDT LMTUSDT MXLUSDT IBKRUSDT CSTOCKUSDT AXPUSDT MDTSTOCKUSDT CRMLUSDT BAUSDT ABBVUSDT TTMIUSDT AGPUUSDT EIDOUSDT EQTUSDT STMUSDT SOXLUSDT'.split()


def default_symbol_lists():
    return {'main':[], 'tradfi':list(TRADFI_SYMBOLS)}

# Additional operator-supplied stocks, ETFs, pre-IPO and commodities.
TRADFI_SYMBOLS=list(dict.fromkeys(TRADFI_SYMBOLS+['SOXLUSDT', 'XAUUSDT', 'CLUSDT', 'KORUUSDT', 'SNDKUSDT', 'SKHYNIXUSDT', 'SKHYUSDT', 'SOXSUSDT', 'SNXXUSDT', 'OKTAUSDT', 'XAGUSDT', 'MUUSDT', 'SPCXUSDT', 'MSTRUSDT', 'TSLAUSDT', 'DRAMUSDT', 'UNITREEUSDT', 'QQQUSDT', 'SAMSUNGUSDT', 'BZUSDT']))

# Operator excluded these three after incomplete hourly S/R history reports.
TRADFI_SYMBOLS=[s for s in TRADFI_SYMBOLS if s not in {'EIDOUSDT','EQTUSDT','STMUSDT'}]

EXCLUDED_SYMBOLS=frozenset({'EIDOUSDT','EQTUSDT','STMUSDT'})

def clean_selections(config):
    """Apply the operator's permanent exclusions, including stale saved forms."""
    config['symbols']=[s for s in config.get('symbols',[]) if s not in EXCLUDED_SYMBOLS]
    config['saved_symbol_lists']={name:[s for s in values if s not in EXCLUDED_SYMBOLS]
        for name,values in config.get('saved_symbol_lists',{}).items()}
    return config
