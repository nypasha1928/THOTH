from pydantic import BaseModel, ConfigDict, Field, model_validator


class Settings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    opposite_action: str = Field(default='reverse',pattern=r'^(reverse|close_only)$')
    strategy: str = Field(default='sr_breakout',pattern=r'^(sr_breakout|sma_cross)$')
    automatic_paper: bool = False
    portfolio_loss_enabled: bool = False
    portfolio_loss_pct: float = Field(default=20, gt=0, le=100, allow_inf_nan=False)
    portfolio_loss_basis: str = Field(default='losing_margin', pattern=r'^(losing_margin|all_margin)$')
    daily_resume_automatic: bool = True
    position_value: float = Field(default=50, gt=0, le=100000, allow_inf_nan=False)
    leverage: int = Field(default=20, ge=1, le=100)
    stop_dollars: float = Field(default=1, gt=0, allow_inf_nan=False)
    sma_exit_enabled: bool = True
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
    timeframe: str = Field(default='1', pattern=r'^(1|D)$')
    pivot_long: int = Field(default=21, ge=1, le=100)
    pivot_short: int = Field(default=5, ge=1, le=100)
    major: bool = True
    minor: bool = True
    sma_enabled: bool = True
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
        if self.strategy=='sr_breakout' and not (self.major or self.minor):
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
