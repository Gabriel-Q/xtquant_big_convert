"""Deterministic IV/Greeks tests; no QMT process or market feed required."""

import datetime
import math
import os
import sys
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src"))

from bigqmt_signal_trader.option_analytics import (
    black_scholes_price,
    calculate_option_analytics,
    implied_volatility,
    normalize_option_type,
    option_greeks,
)
from bigqmt_signal_trader.xtquant_compat import BigQmtXtData


class BlackScholesMathTest(unittest.TestCase):
    def test_option_type_normalization_covers_qmt_detail_values(self):
        for raw in ("C", "call", "CALL", "认购", "购"):
            self.assertEqual(normalize_option_type(raw), "C")
        for raw in ("P", "put", "PUT", "认沽", "沽"):
            self.assertEqual(normalize_option_type(raw), "P")
        with self.assertRaises(ValueError):
            normalize_option_type("future")

    def test_put_call_parity_with_dividend(self):
        spot, strike, years, rate, dividend, sigma = 100.0, 103.0, 0.75, 0.03, 0.01, 0.24
        call = black_scholes_price("C", spot, strike, years, rate, sigma, dividend)
        put = black_scholes_price("P", spot, strike, years, rate, sigma, dividend)
        parity = spot * math.exp(-dividend * years) - strike * math.exp(-rate * years)
        self.assertAlmostEqual(call - put, parity, places=11)

    def test_iv_round_trip_is_stable_for_call_and_put(self):
        for kind in ("C", "P"):
            for sigma in (0.08, 0.25, 0.80):
                price = black_scholes_price(
                    kind, 3.055, 3.0, 22.0 / 365.0, 0.016883, sigma, 0.0
                )
                solved = implied_volatility(
                    kind, 3.055, 3.0, price, 22.0 / 365.0, 0.016883, 0.0
                )
                self.assertAlmostEqual(solved, sigma, places=7)

    def test_iv_rejects_a_price_outside_no_arbitrage_bounds(self):
        with self.assertRaisesRegex(ValueError, "no-arbitrage bounds"):
            implied_volatility("C", 3.0, 2.0, 0.2, 30.0 / 365.0, 0.0)

    def test_greek_units_are_explicit_and_consistent(self):
        greeks = option_greeks("CALL", 100, 100, 1.0, 0.05, 0.2, 0.02)
        self.assertGreater(greeks["delta"], 0)
        self.assertGreater(greeks["gamma"], 0)
        self.assertAlmostEqual(greeks["vega_1pct"], greeks["vega"] * 0.01)
        self.assertAlmostEqual(greeks["rho_1pct"], greeks["rho"] * 0.01)
        self.assertAlmostEqual(greeks["theta_per_day"], greeks["theta_per_year"] / 365.0)

    def test_combined_analytics_reprices_the_observed_option(self):
        observed = black_scholes_price("P", 100, 102, 45.0 / 365.0, 0.02, 0.31)
        result = calculate_option_analytics("PUT", 100, 102, observed, 45, 0.02)
        self.assertAlmostEqual(result["implied_volatility"], 0.31, places=7)
        self.assertAlmostEqual(result["theoretical_price"], observed, places=9)
        self.assertEqual(result["option_type"], "P")
        self.assertLess(result["delta"], 0)


class _UnusedClient(object):
    local_cache_config = {"enabled": False}


class _FakeOptionData(BigQmtXtData):
    def __init__(self):
        super(_FakeOptionData, self).__init__(_UnusedClient())
        self.as_of = datetime.datetime(2026, 9, 1, 15, 0, 0)
        self.underlying_price = 3.055
        self.call_price = black_scholes_price(
            "C", self.underlying_price, 3.0, 22.0 / 365.0, 0.016883, 0.25
        )
        self.put_price = black_scholes_price(
            "P", self.underlying_price, 3.1, 22.0 / 365.0, 0.016883, 0.30
        )
        self.details = {
            "CALL.SHO": {
                "ExpireDate": 20260923,
                "OptExercisePrice": 3.0,
                "OptUndlCode": "510050",
                "OptUndlMarket": "SH",
                "OptUndlRiskFreeRate": 0.016883,
                "optType": "CALL",
            },
            "PUT.SHO": {
                "ExpireDate": 20260923,
                "OptExercisePrice": 3.1,
                "OptUndlCode": "510050",
                "OptUndlMarket": "SH",
                "OptUndlRiskFreeRate": 0.016883,
                "optType": "PUT",
            },
        }
        self.prices = {
            "510050.SH": {"close": [self.underlying_price]},
            "CALL.SHO": {"close": [self.call_price]},
            "PUT.SHO": {"close": [self.put_price]},
        }
        self.last_market_request = None

    def get_option_detail_data(self, stockcode):
        return self.details[stockcode]

    def get_option_list(self, undl_code, dedate, opttype="", isavailavle=False):
        return ["CALL.SHO", "PUT.SHO"]

    def get_market_data_ex(self, **kwargs):
        self.last_market_request = kwargs
        return {code: self.prices.get(code) for code in kwargs["stock_list"]}


class ContractAnalyticsTest(unittest.TestCase):
    def test_one_contract_uses_one_fast_close_request_and_detail_defaults(self):
        data = _FakeOptionData()
        result = data.get_option_analytics("CALL.SHO", as_of=data.as_of)

        self.assertEqual(data.last_market_request["field_list"], ["close"])
        self.assertEqual(
            data.last_market_request["stock_list"], ["CALL.SHO", "510050.SH"]
        )
        self.assertFalse(data.last_market_request["fill_data"])
        self.assertAlmostEqual(result["implied_volatility"], 0.25, places=7)
        self.assertEqual(result["underlying_code"], "510050.SH")
        self.assertEqual(result["expiry_date"], "20260923")
        self.assertAlmostEqual(result["days_to_expiry"], 22.0)
        self.assertEqual(result["option_price_source"], "1m_close")

    def test_explicit_prices_skip_market_data(self):
        data = _FakeOptionData()
        result = data.get_option_analytics(
            "PUT.SHO",
            option_price=data.put_price,
            underlying_price=data.underlying_price,
            as_of=data.as_of,
        )

        self.assertIsNone(data.last_market_request)
        self.assertAlmostEqual(result["implied_volatility"], 0.30, places=7)
        self.assertEqual(result["option_price_source"], "argument")
        self.assertLess(result["delta"], 0)

    def test_chain_keeps_bad_contracts_as_errors(self):
        data = _FakeOptionData()
        data.prices["PUT.SHO"] = {"close": [10.0]}
        result = data.get_option_chain_analytics(
            "510050.SH", "202609", as_of=data.as_of
        )

        self.assertEqual(result["count"], 2)
        self.assertEqual(result["valid_count"], 1)
        self.assertEqual(result["error_count"], 1)
        self.assertNotIn("analytics_error", result["contracts"][0])
        self.assertIn("no-arbitrage bounds", result["contracts"][1]["analytics_error"])


if __name__ == "__main__":
    unittest.main()
