from pydantic import BaseModel, ConfigDict, Field, model_validator


class RSISettings(BaseModel):
    model_config=ConfigDict(extra='forbid')
    calculation_mode: str=Field(default='corrected',pattern=r'^(corrected|uploaded_pine)$')
    source: str=Field(default='close',pattern=r'^(close|open|high|low|hlc3|ohlc4)$')
    rsi_length: int=Field(default=14,ge=2,le=100)
    smooth: bool=True
    smooth_period: int=Field(default=4,ge=1,le=100)
    ma_type: str=Field(default='Ema',pattern=r'^(SMA|Hull|Ema|Wma|Dema|RMA|LINREG|TEMA|ALMA|T3)$')
    alma_sigma: int=Field(default=6,ge=1,le=100)
    max_data: int=Field(default=3000,ge=50,le=3000)
    max_iterations: int=Field(default=1000,ge=1,le=1000)
    volume_filter: bool=True
    volume_period: int=Field(default=20,ge=1,le=500)
    volume_multiplier: float=Field(default=1,ge=.5,le=5,allow_inf_nan=False)
    volume_for_static: bool=True
    vwap_filter: bool=True
    vwap_source: str=Field(default='hlc3',pattern=r'^(close|open|high|low|hlc3|ohlc4)$')
    vwap_anchor: str=Field(default='D',pattern=r'^(1|3|5|15|30|60|240|D|W|M)$')
    sma_filter: bool=True
    sma_fast: int=Field(default=7,ge=1,le=500)
    sma_slow: int=Field(default=50,ge=1,le=500)
    histogram_filter: bool=False
    macd_fast: int=Field(default=12,ge=1,le=500)
    macd_slow: int=Field(default=26,ge=1,le=500)
    macd_signal: int=Field(default=9,ge=1,le=500)
    bull_hist_positive: bool=True
    bear_hist_negative: bool=True
    static_signals: bool=True
    ml_signals: bool=True


class Settings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    opposite_action: str = Field(default='reverse',pattern=r'^(reverse|close_only)$')
    strategy: str = Field(default='sr_breakout',pattern=r'^(sr_breakout|sma_cross|sr_hourly_intrabar|rsi_hourly_intrabar)$')
    rsi: RSISettings=Field(default_factory=RSISettings)
    automatic_paper: bool = False
    portfolio_loss_enabled: bool = False
    portfolio_loss_pct: float = Field(default=20, gt=0, le=100, allow_inf_nan=False)
    portfolio_loss_basis: str = Field(default='losing_margin', pattern=r'^(losing_margin|all_margin)$')
    daily_resume_automatic: bool = True
    position_value: float = Field(default=50, gt=0, le=100000, allow_inf_nan=False)
    leverage: int = Field(default=20, ge=1, le=100)
    stop_dollars: float = Field(default=1, gt=0, allow_inf_nan=False)
    take_profit_enabled: bool = False
    take_profit_dollars: float = Field(default=1,gt=0,le=1000000,allow_inf_nan=False)
    sma_exit_enabled: bool = True
    pnl_trailing_enabled: bool = False
    pnl_trailing_activation_dollars: float = Field(default=1,gt=0,le=1000000,allow_inf_nan=False)
    pnl_trailing_drop_dollars: float = Field(default=.1,gt=0,le=1000000,allow_inf_nan=False)
    trailing_enabled: bool = False
    trailing_activation_basis: str = Field(default='margin',pattern=r'^(price|margin)$')
    trailing_activation_pct: float = Field(default=10,ge=0,le=10000,allow_inf_nan=False)
    trailing_distance_pct: float = Field(default=1,gt=0,lt=100,allow_inf_nan=False)
    trailing_distance_basis: str = Field(default='price',pattern=r'^(price|margin)$')
    daily_loss_pct: float = Field(default=3, gt=0, le=100, allow_inf_nan=False)
    max_positions: int = Field(default=3, ge=1, le=100)
    loss_streak_scope: str = Field(default='per_coin',pattern=r'^(per_coin|account)$')
    consecutive_losses: int = Field(default=3, ge=1, le=100)
    pause_minutes: int = Field(default=60, ge=1, le=10080)
    timeframe: str = Field(default='1', pattern=r'^(1|5|60|240|D)$')
    pivot_long: int = Field(default=21, ge=1, le=100)
    pivot_short: int = Field(default=5, ge=1, le=100)
    major: bool = True
    minor: bool = True
    sma_7_50_filter_enabled: bool = False
    sma_enabled: bool = False
    sma_length: int = Field(default=200, ge=1, le=500)
    symbols: list[str] = Field(default_factory=lambda: ['BTCUSDT', 'ETHUSDT', 'SOLUSDT'], max_length=300)

    @model_validator(mode='after')
    def valid_symbols(self):
        import re
        if len(set(self.symbols)) != len(self.symbols):
            raise ValueError('Duplicate symbols')
        if any(not re.fullmatch(r'[A-Z0-9]{2,30}USDT', s) for s in self.symbols):
            raise ValueError('Expected Bybit USDT symbol names')
        if self.trailing_enabled and self.trailing_activation_pct>=(100 if self.trailing_activation_basis=='price' else self.leverage*100):
            raise ValueError('Trailing activation must allow a positive short activation price')
        if self.strategy in ('sr_hourly_intrabar','rsi_hourly_intrabar'):
            self.timeframe='60'
            self.sma_exit_enabled=True
        if self.strategy=='rsi_hourly_intrabar' and not (self.rsi.static_signals or self.rsi.ml_signals):
            raise ValueError('Enable at least one RSI entry signal family')
        if self.strategy in ('sr_breakout','sr_hourly_intrabar') and not (self.major or self.minor):
            raise ValueError('Enable at least one level family')
        return self


class PaperOrder(BaseModel):
    model_config = ConfigDict(extra='forbid')
    symbol: str = Field(pattern=r'^[A-Z0-9]{2,30}USDT$')
    side: str = Field(pattern=r'^(Long|Short)$')
    price: float = Field(gt=0, allow_inf_nan=False)
    signal_id: str = Field(min_length=1, max_length=100)


class Marks(BaseModel):
    model_config = ConfigDict(extra='forbid')
    prices: dict[str, float]

    @model_validator(mode='after')
    def positive_prices(self):
        import math
        if any(not math.isfinite(v) or v <= 0 for v in self.prices.values()):
            raise ValueError('Marks must be positive finite prices')
        return self


class TestnetSettings(Settings):
    daily_resume_automatic: bool = False
    margin_mode: str = Field(default='isolated', pattern=r'^(unconfirmed|isolated|cross)$')
    dedicated_account: bool = False

    @model_validator(mode='after')
    def paper_disabled(self):
        if self.automatic_paper:
            raise ValueError('Testnet configuration cannot enable automatic paper mode')
        return self


class TestnetActivation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    confirmation: str
