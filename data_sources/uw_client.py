import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from config import UW_API_TOKEN
import logging

UW_BASE_URL = "https://api.unusualwhales.com/api"
logger = logging.getLogger(__name__)


def uw_available() -> bool:
    """Check if UW API token is configured."""
    return bool(UW_API_TOKEN)


class UnusualWhalesClient:
    def __init__(self, api_key: str = UW_API_TOKEN):
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json"
        })

    def _get(self, path: str, params: dict = None) -> dict | list:
        if params is None:
            params = {}
        try:
            r = self.session.get(f"{UW_BASE_URL}{path}", params=params, timeout=10)
            if r.status_code != 200:
                logger.warning(f"UW API Error ({r.status_code}) for {path}: {r.text[:100]}")
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.error(f"Failed to fetch {path} from UW: {e}")
            return {}

    # --- 1. Smart Money & Institutions (Long-Term Conviction) ---

    def get_insider(self, ticker: str) -> dict:
        """GET /api/insider/{ticker}"""
        return self._get(f"/insider/{ticker}")

    def get_congress_holders(self, ticker: str) -> dict:
        """GET /api/politician-portfolios/holders/{ticker}"""
        return self._get(f"/politician-portfolios/holders/{ticker}")

    def get_institution_ownership(self, ticker: str) -> dict:
        """GET /api/institution/{ticker}/ownership"""
        return self._get(f"/institution/{ticker}/ownership")

    # --- 2. Squeeze Potential & Resistance (Swing Catalysts) ---

    def get_short_interest(self, ticker: str) -> dict:
        """GET /api/shorts/{ticker}/interest-float/v2"""
        return self._get(f"/shorts/{ticker}/interest-float/v2")

    def get_ftds(self, ticker: str) -> dict:
        """GET /api/shorts/{ticker}/ftds"""
        return self._get(f"/shorts/{ticker}/ftds")

    def get_darkpool(self, ticker: str) -> dict:
        """GET /api/darkpool/{ticker}"""
        return self._get(f"/darkpool/{ticker}")

    # --- 3. Seasonality & Volatility Anomalies (Swing Timing) ---

    def get_seasonality(self, ticker: str) -> dict:
        """GET /api/seasonality/{ticker}/monthly"""
        return self._get(f"/seasonality/{ticker}/monthly")

    def get_volatility_anomaly(self, ticker: str) -> dict:
        """GET /api/stock/{ticker}/volatility/anomaly"""
        return self._get(f"/stock/{ticker}/volatility/anomaly")

    # --- 4. Broad Options Sentiment (Not 0DTE) ---

    def get_risk_reversal_skew(self, ticker: str) -> dict:
        """GET /api/stock/{ticker}/historical-risk-reversal-skew"""
        return self._get(f"/stock/{ticker}/historical-risk-reversal-skew")

    def get_option_stance(self, ticker: str) -> dict:
        """GET /api/stock/{ticker}/option-stance"""
        return self._get(f"/stock/{ticker}/option-stance")

    def get_comprehensive_profile(self, ticker: str) -> dict:
        """Fetch all UW data for a ticker in parallel (fast)."""
        endpoints = {
            "insider": lambda: self.get_insider(ticker),
            "congress_holders": lambda: self.get_congress_holders(ticker),
            "institution_ownership": lambda: self.get_institution_ownership(ticker),
            "short_interest": lambda: self.get_short_interest(ticker),
            "ftds": lambda: self.get_ftds(ticker),
            "darkpool": lambda: self.get_darkpool(ticker),
            "seasonality": lambda: self.get_seasonality(ticker),
            "vol_anomaly": lambda: self.get_volatility_anomaly(ticker),
            "skew": lambda: self.get_risk_reversal_skew(ticker),
            "option_stance": lambda: self.get_option_stance(ticker),
        }
        result = {}
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = {pool.submit(fn): key for key, fn in endpoints.items()}
            for future in as_completed(futures):
                key = futures[future]
                try:
                    result[key] = future.result()
                except Exception as e:
                    logger.error(f"UW {key} failed for {ticker}: {e}")
                    result[key] = {}
        return result
