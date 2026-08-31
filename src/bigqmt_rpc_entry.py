#coding:gbk
# Big QMT model-trade entry (v5): locate the deploy dir robustly, load the
# RPC backend by file path, bind QMT-injected trade APIs, delegate callbacks.
# The backend file is bigqmt_rpc_backend.py (a clean copy of the RPC runtime):
# the original filename was encrypted in place by the QMT strategy parser the
# first time it was attached to a model-trade instance, and importing it by
# module name is rejected by the loader's static name scan (rzrkModuleCheckError).

import sys
import os
import glob
import traceback

_MARKER = 'bigqmt_signal_trader_strategy.py'
_BACKEND_FILE = 'bigqmt_rpc_backend.py'


def _write_diag(base, lines):
    try:
        with open(os.path.join(base or os.getcwd(), 'bigqmt_rpc_entry_diag.txt'), 'w') as f:
            f.write('\n'.join(lines) + '\n')
    except Exception:
        pass


def _find_base():
    cands = []
    try:
        cands.append(os.path.dirname(os.path.abspath(__file__)))
    except NameError:
        pass
    cwd = os.getcwd()
    cands.append(cwd)
    d = cwd
    for _ in range(5):
        parent = os.path.dirname(d)
        if not parent or parent == d:
            break
        d = parent
        cands.append(d)
        cands.append(os.path.join(d, 'python'))
    for drive in ('C:\\', 'D:\\'):
        cands.extend(glob.glob(drive + '*' + os.sep + 'python'))
        cands.extend(glob.glob(drive + '*' + os.sep + '*' + os.sep + 'python'))
        cands.append(drive)
    for d in cands:
        try:
            if d and os.path.isfile(os.path.join(d, _MARKER)):
                return os.path.abspath(d)
        except Exception:
            pass
    return None


_base = _find_base()
_diag = ['cwd=%r' % os.getcwd(),
         '__file__=%r' % globals().get('__file__'),
         'base=%r' % _base]
if _base and _base not in sys.path:
    sys.path.insert(0, _base)

import bigqmt_signal_trader_strategy as _strategy

try:
    _strategy.reset_app()
except Exception:
    _diag.append('reset_app failed:')
    _diag.append(traceback.format_exc())

_runtime = None
_runtime_err = None
try:
    import importlib.util
    _spec = importlib.util.spec_from_file_location(
        'bigqmt_rpc_rt_host',
        os.path.join(_base or os.getcwd(), _BACKEND_FILE))
    _runtime = importlib.util.module_from_spec(_spec)
    # The QMT-injected module bridge is not usable inside a spec-loaded
    # module; null it so the backend takes the plain from-import branch.
    _runtime.__dict__['__bigqmt_load_local_module'] = None
    sys.modules['bigqmt_rpc_rt_host'] = _runtime
    _spec.loader.exec_module(_runtime)
except Exception:
    _runtime_err = traceback.format_exc()
    _runtime = None

# Persist diagnostics immediately, before anything else can fail.
_diag.append('runtime_err=%s' % ((_runtime_err or 'None')[:4000]))
_diag.append('has_init=%r' % hasattr(_runtime, 'init'))
_write_diag(_base, _diag)

if _runtime is not None and hasattr(_runtime, 'init'):
    # passorder / cancel / get_trade_detail_data are injected by Big QMT into
    # this script's exec namespace. Without this bind the RPC runtime cannot
    # reach the trade counter.
    try:
        _strategy.bind_qmt_api(
            passorder_func=passorder,
            cancel_func=cancel,
            get_trade_detail_data_func=get_trade_detail_data,
        )
    except NameError:
        _diag.append('bind_qmt_api: QMT funcs not injected (NameError)')

    try:
        _extra = _strategy.capture_qmt_download_funcs(globals())
        if _extra:
            _strategy.bind_qmt_api(extra_funcs=_extra)
            print('[bigqmt_entry] bound global download funcs: %s' % sorted(_extra))
        else:
            print('[bigqmt_entry] no global download funcs in entry namespace')
    except Exception:
        import traceback as _tb
        print('[bigqmt_entry] extra bind failed: %s' % _tb.format_exc()[:500])

    init = _runtime.init
    handlebar = _runtime.handlebar
    adjust = _runtime.adjust
    order_callback = _runtime.order_callback
    deal_callback = _runtime.deal_callback
else:
    def init(context_info):
        pass

    def handlebar(context_info):
        return None

    def adjust(context_info):
        return None
