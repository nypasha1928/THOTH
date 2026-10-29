from pydantic import BaseModel, ConfigDict, Field, model_validator
from .tradfi import default_symbol_lists,EXCLUDED_SYMBOLS


class RSISettings(BaseModel):
    model_config=ConfigDict(extra='forbid')
    signal_rules: str=Field(default='original_six',pattern=r'^(original_six|eight_crossings)$')
    calculation_mode: str=Field(default='fixed',pattern=r'^(fixed|corrected|uploaded_pine)$')
    fixed_long: float=Field(default=56.68,gt=0,lt=100,allow_inf_nan=False)
    fixed_short: float=Field(default=42.38,gt=0,lt=100,allow_inf_nan=False)
    source: str=Field(default='close',pattern=r'^(close|open|high|low|hlc3|ohlc4)$')
    rsi_length: int=Field(default=14,ge=2,le=100)
    smooth: bool=True
    smooth_period: int=Field(default=4,ge=1,le=100)
    ma_type: str=Field(default='Ema',pattern=r'^(SMA|Hull|Ema|Wma|Dema|RMA|LINREG|TEMA|ALMA|T3)$')
    alma_sigma: int=Field(default=6,ge=1,le=100)
    max_data: int=Field(default=3000,ge=50,le=3000)
    max_iterations: int=Field(default=1000,ge=1,le=1000)
    volume_filter: bool=False
    volume_period: int=Field(default=20,ge=1,le=500)
    volume_multiplier: float=Field(default=1,ge=.5,le=5,allow_inf_nan=False)
    volume_for_static: bool=False
    vwap_filter: bool=False
    vwap_source: str=Field(default='hlc3',pattern=r'^(close|open|high|low|hlc3|ohlc4)$')
    vwap_anchor: str=Field(default='D',pattern=r'^(1|3|5|15|30|60|240|D|W|M)$')
    sma_filter: bool=False
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

    @model_validator(mode='after')
    def fixed_order(self):
        if self.fixed_short>=self.fixed_long:raise ValueError('Fixed Short must be below Fixed Long')
        return self


class VolumeDeltaSettings(BaseModel):
    model_config=ConfigDict(extra='forbid')
    sma_filter: bool=True
    bull_type: str=Field(default='strong',pattern=r'^(strong|weak|all)$')
    bear_type: str=Field(default='strong',pattern=r'^(strong|weak|all)$')
    bullish: bool=True
    bearish: bool=True


class OrderBlockSettings(BaseModel):
    model_config=ConfigDict(extra='forbid')
    filter_breaks: bool=True
    bullish_formed: bool=True
    bearish_formed: bool=True
    supply_breakout: bool=True
    demand_breakdown: bool=True


class Settings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    opposite_action: str = Field(default='reverse',pattern=r'^(reverse|close_only)$')
    strategy: str = Field(default='sr_breakout',pattern=r'^(sr_breakout|sma_cross|sma_7_50|sr_sma_7_50|sr_rsi_daily|sr_inverse_intrabar|volume_delta_intrabar|sr_rsi_volume_gate|sr_order_block_gate|sr_volume_intrabar|sma_7_50_intrabar|order_block_intrabar|order_block_sr_intrabar|order_block_sr_1h|order_block_sr_1h_inverted_close|order_block_sr_1h_close|ob_sr_hour_gate|ob_sr_minute_hourly|sr_hourly_intrabar|rsi_hourly_intrabar)$')
    rsi: RSISettings=Field(default_factory=RSISettings)
    volume_delta: VolumeDeltaSettings=Field(default_factory=VolumeDeltaSettings)
    order_block: OrderBlockSettings=Field(default_factory=OrderBlockSettings)
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
    sma_ob_exit_enabled: bool = True
    pnl_trailing_enabled: bool = False
    pnl_trailing_activation_dollars: float = Field(default=1,gt=0,le=1000000,allow_inf_nan=False)
    pnl_trailing_drop_dollars: float = Field(default=.1,gt=0,le=1000000,allow_inf_nan=False)
    equity_trailing_resume_automatic: bool = True
    daily_equity_trailing_enabled: bool = False
    daily_equity_trailing_drop_dollars: float = Field(default=1,gt=0,le=1000000,allow_inf_nan=False)
    three_day_equity_trailing_enabled: bool = False
    three_day_equity_trailing_drop_dollars: float = Field(default=1,gt=0,le=1000000,allow_inf_nan=False)
    weekly_equity_trailing_enabled: bool = False
    weekly_equity_trailing_drop_dollars: float = Field(default=1,gt=0,le=1000000,allow_inf_nan=False)
    coin_pnl_trailing_enabled: bool = False
    coin_pnl_trailing_activation_dollars: float = Field(default=1,gt=0,le=1000000,allow_inf_nan=False)
    coin_pnl_trailing_drop_dollars: float = Field(default=.1,gt=0,le=1000000,allow_inf_nan=False)
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
    minor: bool = False
    sma_7_50_filter_enabled: bool = False
    sma_enabled: bool = False
    sma_length: int = Field(default=200, ge=1, le=500)
    sr_volume_period: int = Field(default=20,ge=2,le=500)
    minute_entry_window_minutes: int = Field(default=5,ge=5,le=15)
    inverse_direction_confirmed: bool = False
    inverse_entry_delay_seconds: int = Field(default=60,ge=0,le=3600)
    symbols: list[str] = Field(default_factory=lambda: ['BTCUSDT', 'ETHUSDT', 'SOLUSDT'], max_length=300)

    active_symbol_list: str = Field(default='main',pattern=r'^(main|tradfi|main_leaders|tradfi_leaders)$')
    saved_symbol_lists: dict[str,list[str]] = Field(default_factory=default_symbol_lists)

    @model_validator(mode='after')
    def valid_symbols(self):
        import re
        self.saved_symbol_lists={**default_symbol_lists(),**self.saved_symbol_lists}
        self.symbols=[s for s in self.symbols if s not in EXCLUDED_SYMBOLS]
        self.saved_symbol_lists={name:[s for s in values if s not in EXCLUDED_SYMBOLS] for name,values in self.saved_symbol_lists.items()}
        if set(self.saved_symbol_lists)-{'main','tradfi','main_leaders','tradfi_leaders'}:
            raise ValueError('Unknown saved symbol list')
        for name,values in self.saved_symbol_lists.items():
            if len(values)>300 or len(set(values))!=len(values) or any(not re.fullmatch(r'[A-Z0-9]{1,30}USDT',s) for s in values):
                raise ValueError('Invalid saved trading symbols')
        # Existing installations migrate their current selection into Main.
        self.saved_symbol_lists={**self.saved_symbol_lists,self.active_symbol_list:list(self.symbols)}
        if len(set(self.symbols)) != len(self.symbols):
            raise ValueError('Duplicate symbols')
        if any(not re.fullmatch(r'[A-Z0-9]{1,30}USDT', s) for s in self.symbols):
            raise ValueError('Expected Bybit USDT symbol names')
        if self.trailing_enabled and self.trailing_activation_pct>=(100 if self.trailing_activation_basis=='price' else self.leverage*100):
            raise ValueError('Trailing activation must allow a positive short activation price')
        if self.strategy in ('order_block_intrabar','order_block_sr_intrabar','order_block_sr_1h','order_block_sr_1h_inverted_close','order_block_sr_1h_close'):
            self.timeframe='60' if self.strategy in ('order_block_sr_1h','order_block_sr_1h_inverted_close','order_block_sr_1h_close') else '240';self.sma_exit_enabled=False
            self.sma_enabled=False;self.sma_7_50_filter_enabled=False
            self.order_block=OrderBlockSettings(bullish_formed=True,bearish_formed=True,supply_breakout=True,demand_breakdown=True,filter_breaks=False)
            if self.strategy in ('order_block_sr_intrabar','order_block_sr_1h','order_block_sr_1h_inverted_close','order_block_sr_1h_close'):self.major=True;self.minor=False;self.sr_volume_period=20
        if self.strategy=='sma_7_50_intrabar':
            self.timeframe='240';self.sma_exit_enabled=False;self.opposite_action='reverse'
            self.sma_enabled=False;self.sma_7_50_filter_enabled=False
        if self.strategy=='sr_volume_intrabar':
            self.timeframe='240';self.sma_exit_enabled=False
            self.sma_enabled=False;self.sma_7_50_filter_enabled=False
        if self.minute_entry_window_minutes not in (5,10,15):raise ValueError('Minute entry window must be 5, 10 or 15 minutes')
        if self.strategy=='ob_sr_minute_hourly':
            self.timeframe='1';self.sma_exit_enabled=False;self.opposite_action='close_only'
            self.sma_enabled=False;self.sma_7_50_filter_enabled=False;self.major=True;self.minor=False
            self.order_block=OrderBlockSettings(filter_breaks=False)
        if self.strategy=='ob_sr_hour_gate':
            self.timeframe='1';self.sma_exit_enabled=False;self.opposite_action='close_only'
            self.sma_enabled=False;self.sma_7_50_filter_enabled=False;self.major=True;self.minor=False
            self.loss_streak_scope='per_coin'
            self.order_block=OrderBlockSettings(filter_breaks=False)
        if self.strategy=='sr_order_block_gate':
            self.sma_length=200
            self.timeframe='5';self.sma_exit_enabled=False;self.loss_streak_scope='per_coin'
            self.sma_enabled=False;self.sma_7_50_filter_enabled=False
            self.order_block=self.order_block.model_copy(update={'filter_breaks':True})
            if not any(getattr(self.order_block,key) for key in ('bullish_formed','bearish_formed','supply_breakout','demand_breakdown')):
                raise ValueError('Enable at least one Order Block signal')
        if self.strategy=='sr_rsi_volume_gate':
            self.timeframe='1';self.sma_exit_enabled=False;self.loss_streak_scope='per_coin'
            self.sma_enabled=False;self.sma_7_50_filter_enabled=False
            self.volume_delta=self.volume_delta.model_copy(update={'sma_filter':True})
            self.rsi=self.rsi.model_copy(update=dict(calculation_mode='fixed',signal_rules='eight_crossings',
                static_signals=True,ml_signals=True,volume_filter=False,volume_for_static=False,
                vwap_filter=False,sma_filter=False,histogram_filter=False))
        if self.strategy in ('volume_delta_intrabar','sr_rsi_volume_gate'):
            self.sma_exit_enabled=False
            if not (self.volume_delta.bullish or self.volume_delta.bearish):raise ValueError('Enable at least one Volume Delta signal direction')
        if self.strategy=='sr_inverse_intrabar':
            if 'timeframe' not in self.model_fields_set:self.timeframe='240'
            if self.timeframe not in ('240','D'):raise ValueError('Inverse S/R entries require 4-hour or Daily timeframe')
            if not self.inverse_direction_confirmed:raise ValueError('Confirm inverse S/R direction: Long signal opens Short; Short signal opens Long')
            self.sma_exit_enabled=True
        if self.strategy=='sr_rsi_daily':
            self.timeframe='D';self.sma_exit_enabled=True
            self.sma_enabled=False;self.sma_7_50_filter_enabled=False
            self.rsi=self.rsi.model_copy(update=dict(calculation_mode='fixed',signal_rules='eight_crossings',
                static_signals=True,ml_signals=True,volume_filter=False,volume_for_static=False,
                vwap_filter=False,sma_filter=False,histogram_filter=False))
        if self.strategy in ('sr_hourly_intrabar','rsi_hourly_intrabar'):
            if 'timeframe' not in self.model_fields_set:self.timeframe='60'
            self.sma_exit_enabled=True
        if self.strategy=='rsi_hourly_intrabar' and not (self.rsi.static_signals or self.rsi.ml_signals):
            raise ValueError('Enable at least one RSI entry signal family')
        # Minor S/R is display-only in every strategy, including legacy saved settings.
        if self.minor:self.major=True  # Migrate legacy minor-only configurations to majors.
        self.minor=False
        if self.strategy in ('sr_breakout','sr_sma_7_50','sr_hourly_intrabar','sr_rsi_daily','sr_inverse_intrabar','sr_rsi_volume_gate','sr_order_block_gate','sr_volume_intrabar') and not (self.major or self.minor):
            raise ValueError('Enable at least one level family')
        return self


class PaperOrder(BaseModel):
    model_config = ConfigDict(extra='forbid')
    symbol: str = Field(pattern=r'^[A-Z0-9]{1,30}USDT$')
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
