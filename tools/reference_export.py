"""Create a TradingView export copy without modifying the supplied reference.

Usage: python tools/reference_export.py /tmp/thoth-export.pine
Paste that generated copy into TradingView, use matching timeframe/symbol,
and export chart data including indicators. Never edit protected originals.
"""
from pathlib import Path
import argparse
import re


def export_source(source):
    overrides={'use_LongMajor':'true','use_LongMinor':'true','use_ShortMajor':'true','use_ShortMinor':'true',
               'signal_type':'"Breakout"','use_sma_7_50_filter':'false','use_sma_200_filter':'true',
               'enable_sma_cross':'false','exit_enable':'false','vol_pass20_enable':'false','vol_thresh_enable':'false'}
    for name,value in overrides.items():
        source,count=re.subn(r'(^'+name+r'\s*=\s*input\.(?:bool|string)\()[^,]+',lambda m:m[1]+value,source,flags=re.M)
        if count!=1:raise ValueError(f'Expected one source input for {name}, found {count}')
    source+='\n// THOTH parity export helpers. Original MPL-2.0 notices above remain applicable.\n'
    for prefix,family in [('L','long'),('S','short')]:
        for direction,short in [('support','sup'),('resistance','res')]:
            for kind,abbrev in [('major','maj'),('minor','min')]:
                source+=f'plot({prefix}_{short}_{abbrev}_cur, title="THOTH_{family}_{direction}_{kind}", display=display.data_window)\n'
    source+='plot(bullish_signal ? 1 : 0, title="THOTH_long_signal", display=display.data_window)\n'
    source+='plot(bearish_signal ? 1 : 0, title="THOTH_short_signal", display=display.data_window)\n'
    return source


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output',type=Path)
    args=parser.parse_args()
    original=Path(__file__).resolve().parents[1]/'reference/support_resistance.pine'
    if args.output.resolve()==original.resolve():raise SystemExit('Refusing to overwrite original reference')
    with args.output.open('x') as output:output.write(export_source(original.read_text()))
    print('Wrote export helper:',args.output)
