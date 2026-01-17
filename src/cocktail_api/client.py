"""
Cocktail Recipe Manager API client with JWT authentication and name-based search.

This module provides a production-ready wrapper around the Cocktail Recipe Manager API
with comprehensive error handling, retries, and name-based cocktail lookup.
"""

import logging
import time
from typing import Dict, List, Optional, Any
from functools import wraps
from difflib import get_close_matches
import httpx
from uuid import UUID

from src.config import settings

logger = logging.getLogger(__name__)

# Retry configuration
MAX_RETRIES = 3
RETRY_DELAY = 1  # seconds

# Event consumption defaults (used to compute liters from people count)
COCKTAILS_PER_PERSON = 3
DEFAULT_COCKTAIL_KINDS = 4  # typical number of cocktail types per event


def retry_on_error(max_retries: int = MAX_RETRIES, delay: float = RETRY_DELAY):
    """Decorator to retry function calls on transient errors."""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except httpx.HTTPStatusError as e:
                    last_exception = e
                    # Retry on 5xx errors and rate limits
                    if e.response.status_code in [429, 500, 502, 503, 504]:
                        wait_time = delay * (2 ** attempt)  # Exponential backoff
                        logger.warning(f"Retry {attempt + 1}/{max_retries} for {func.__name__} after {wait_time}s: {e}")
                        time.sleep(wait_time)
                        continue
                    else:
                        raise
                except Exception as e:
                    last_exception = e
                    if attempt < max_retries - 1:
                        wait_time = delay * (2 ** attempt)
                        logger.warning(f"Retry {attempt + 1}/{max_retries} for {func.__name__} after {wait_time}s: {e}")
                        time.sleep(wait_time)
                        continue
                    raise
            raise last_exception
        return wrapper
    return decorator


class CocktailAPIClient:
    """Client for Cocktail Recipe Manager API with JWT authentication."""

    def __init__(self):
        self.base_url = settings.COCKTAIL_API_URL.rstrip('/')
        self.email = settings.COCKTAIL_API_EMAIL
        self.password = settings.COCKTAIL_API_PASSWORD.get_secret_value()
        self.location = settings.COCKTAIL_API_LOCATION
        self.token: Optional[str] = None
        self._cocktail_cache: Optional[List[Dict]] = None
        self._cache_timestamp: Optional[float] = None
        self._cache_ttl = 300  # Cache for 5 minutes

    async def _ensure_authenticated(self):
        """Ensure we have a valid token, re-authenticate if needed."""
        if not self.token:
            await self.authenticate()

    async def authenticate(self):
        """Login and store JWT token."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.base_url}/auth/jwt/login",
                    data={
                        "username": self.email,
                        "password": self.password
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"}
                )
                response.raise_for_status()
                data = response.json()
                self.token = data["access_token"]
                logger.info("Successfully authenticated with Cocktail API")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                logger.error("Authentication failed: Invalid credentials")
                raise ValueError("Invalid Cocktail API credentials")
            raise
        except Exception as e:
            logger.error(f"Authentication error: {e}")
            raise

    def _headers(self) -> Dict[str, str]:
        """Get headers with auth token."""
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }

    async def _refresh_cache_if_needed(self):
        """Refresh cocktail cache if it's expired."""
        if (self._cocktail_cache is None or
            self._cache_timestamp is None or
            time.time() - self._cache_timestamp > self._cache_ttl):
            await self.get_all_cocktails()

    @retry_on_error()
    async def get_all_cocktails(self) -> List[Dict]:
        """Get all cocktails for name matching."""
        await self._ensure_authenticated()

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/cocktail-recipes/",
                    headers=self._headers()
                )

                if response.status_code == 401:
                    # Token expired, re-authenticate
                    await self.authenticate()
                    response = await client.get(
                        f"{self.base_url}/cocktail-recipes/",
                        headers=self._headers()
                    )

                response.raise_for_status()
                cocktails = response.json()

                # Update cache
                self._cocktail_cache = cocktails
                self._cache_timestamp = time.time()

                logger.info(f"Retrieved {len(cocktails)} cocktails")
                return cocktails
        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to get cocktails: {e}")
            raise

    async def search_cocktail_by_name(self, name: str) -> List[Dict]:
        """
        Search cocktails by name with fuzzy matching.

        Args:
            name: Cocktail name to search for

        Returns:
            List of matching cocktails (empty if no matches)
        """
        await self._refresh_cache_if_needed()

        if not self._cocktail_cache:
            return []

        name_lower = name.lower().strip()
        exact_matches = []
        partial_matches = []

        for cocktail in self._cocktail_cache:
            cocktail_name = cocktail.get("name", "").lower()

            # Exact match
            if cocktail_name == name_lower:
                exact_matches.append(cocktail)
            # Partial match (contains)
            elif name_lower in cocktail_name or cocktail_name in name_lower:
                partial_matches.append(cocktail)

        # Return exact matches first, then partial matches
        if exact_matches:
            return exact_matches
        elif partial_matches:
            return partial_matches
        else:
            # Try fuzzy matching
            cocktail_names = {c.get("name"): c for c in self._cocktail_cache}
            close_matches = get_close_matches(
                name,
                cocktail_names.keys(),
                n=5,
                cutoff=0.6
            )
            return [cocktail_names[match] for match in close_matches]

    @retry_on_error()
    async def get_cocktail(self, cocktail_id: str) -> Dict:
        """Get cocktail details by ID (internal use)."""
        await self._ensure_authenticated()

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/cocktail-recipes/{cocktail_id}",
                    headers=self._headers()
                )

                if response.status_code == 401:
                    await self.authenticate()
                    response = await client.get(
                        f"{self.base_url}/cocktail-recipes/{cocktail_id}",
                        headers=self._headers()
                    )

                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.warning(f"Cocktail {cocktail_id} not found")
            raise

    @retry_on_error()
    async def get_inventory_items(self, location: Optional[str] = None,
                                  item_type: Optional[str] = None,
                                  q: Optional[str] = None) -> List[Dict]:
        """Get inventory items."""
        await self._ensure_authenticated()

        params = {}
        if location:
            params["location"] = location
        if item_type:
            params["item_type"] = item_type
        if q:
            params["q"] = q

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/inventory/items",
                    headers=self._headers(),
                    params=params
                )

                if response.status_code == 401:
                    await self.authenticate()
                    response = await client.get(
                        f"{self.base_url}/inventory/items",
                        headers=self._headers(),
                        params=params
                    )

                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to get inventory items: {e}")
            raise

    @retry_on_error()
    async def consume_batch(self, cocktail_id: str, liters: float,
                           location: Optional[str] = None,
                           include_garnish: bool = True,
                           include_optional: bool = True,
                           reason: Optional[str] = None,
                           source_type: Optional[str] = "telegram_event",
                           source_id: Optional[int] = None) -> Dict:
        """
        Reduce inventory for a cocktail by liters consumed (one API call for all ingredients).

        POST /inventory/cocktails/{cocktail_id}/consume-batch
        The API converts ml used into bottles using Bottle.volume_ml.
        """
        await self._ensure_authenticated()
        if not location:
            location = self.location

        payload: Dict[str, Any] = {
            "liters": liters,
            "location": location,
            "include_garnish": include_garnish,
            "include_optional": include_optional,
        }
        if reason:
            payload["reason"] = reason
        if source_type:
            payload["source_type"] = source_type
        if source_id is not None:
            payload["source_id"] = source_id

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.base_url}/inventory/cocktails/{cocktail_id}/consume-batch",
                    json=payload,
                    headers=self._headers()
                )

                if response.status_code == 401:
                    await self.authenticate()
                    response = await client.post(
                        f"{self.base_url}/inventory/cocktails/{cocktail_id}/consume-batch",
                        json=payload,
                        headers=self._headers()
                    )

                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 403:
                logger.error("Insufficient permissions: Bot user must be superuser")
                raise ValueError("Bot user does not have permission to create inventory movements")
            logger.error(f"Failed to consume batch: {e}")
            raise

    @retry_on_error()
    async def reduce_stock(self, inventory_item_id: str, amount: float,
                          location: str, reason: str,
                          source_type: Optional[str] = None,
                          source_id: Optional[int] = None) -> Dict:
        """Reduce inventory via POST /inventory/movements (legacy; prefer consume_batch)."""
        await self._ensure_authenticated()
        payload: Dict[str, Any] = {
            "location": location,
            "inventory_item_id": str(inventory_item_id),
            "change": -abs(amount),
            "reason": reason,
        }
        if source_type:
            payload["source_type"] = source_type
        if source_id is not None:
            payload["source_id"] = source_id
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/inventory/movements",
                json=payload,
                headers=self._headers()
            )
            if response.status_code == 401:
                await self.authenticate()
                response = await client.post(
                    f"{self.base_url}/inventory/movements",
                    json=payload,
                    headers=self._headers()
                )
            response.raise_for_status()
            return response.json()

    def _recipe_total_ml(self, recipe_ingredients: List[Dict]) -> float:
        """Sum liquid volume from recipe (ml and oz) to derive liters for one serving."""
        total_ml = 0.0
        oz_ml = 29.5735
        for ing in recipe_ingredients or []:
            q = float(ing.get("quantity") or 0)
            u = (ing.get("unit") or "").lower()
            if u == "ml":
                total_ml += q
            elif u in ("oz", "fl oz"):
                total_ml += q * oz_ml
            elif u == "dash":
                total_ml += q * 0.9  # ~0.9ml per dash
        return total_ml

    async def process_cocktail_order(self, cocktail_name: str, location: Optional[str] = None,
                                    event_id: Optional[int] = None,
                                    servings: int = 1,
                                    liters: Optional[float] = None,
                                    people: Optional[int] = None,
                                    cocktail_kinds: Optional[int] = None) -> Dict:
        """
        Process a cocktail order by name using consume-batch (one API call).

        Servings (and thus liters) can be derived from headcount:
        - Total cocktails = people * COCKTAILS_PER_PERSON (default 3 per person)
        - This cocktail type gets: total / cocktail_kinds (default 4 kinds per event)
        So: servings = (people * 3) / max(1, cocktail_kinds or 4)

        Args:
            cocktail_name: Name of the cocktail (will be resolved to ID)
            location: Location for inventory (defaults to configured location)
            event_id: Optional event ID for tracking
            servings: Explicit number of servings. Ignored if people is set.
            liters: Override – total liters consumed. If None, computed from recipe for 1 serving * servings.
            people: If set, servings = (people * COCKTAILS_PER_PERSON) / cocktail_kinds (or 4).
            cocktail_kinds: Number of cocktail types at the event; used with people. Default 4.

        Returns:
            Summary compatible with existing callers: cocktail, movements, errors.
            movements is derived from API response when available.
        """
        if not location:
            location = self.location

        # Compute servings from people when provided
        if people is not None and people > 0:
            kinds = max(1, cocktail_kinds or DEFAULT_COCKTAIL_KINDS)
            total_cocktails = people * COCKTAILS_PER_PERSON
            servings = max(1, round(total_cocktails / kinds))

        matches = await self.search_cocktail_by_name(cocktail_name)
        if not matches:
            raise ValueError(f"Cocktail '{cocktail_name}' not found")
        if len(matches) > 1:
            raise ValueError(
                f"Multiple cocktails match '{cocktail_name}': {[m['name'] for m in matches]}"
            )

        cocktail = matches[0]
        cocktail_id = cocktail["id"]
        cocktail_name_actual = cocktail["name"]

        # Resolve liters: explicit override, or from recipe * servings
        if liters is not None and liters > 0:
            total_liters = liters
        else:
            details = await self.get_cocktail(cocktail_id)
            total_ml = self._recipe_total_ml(details.get("recipe_ingredients") or [])
            total_liters = (total_ml / 1000.0) * max(1, servings)
            if total_liters <= 0:
                total_liters = 0.1 * max(1, servings)  # fallback 100ml per serving

        reason = f"Cocktail served: {cocktail_name_actual}" + (f" ({servings} serving(s))" if servings > 1 else "")

        try:
            data = await self.consume_batch(
                cocktail_id=cocktail_id,
                liters=total_liters,
                location=location,
                include_garnish=True,
                include_optional=True,
                reason=reason,
                source_type="telegram_event",
                source_id=event_id
            )
        except Exception as e:
            return {
                "cocktail": cocktail_name_actual,
                "cocktail_id": cocktail_id,
                "movements": [],
                "errors": [str(e)]
            }

        # Normalize response for existing callers (movements list, errors)
        movements = []
        if isinstance(data, dict):
            # Response shape may vary; use common keys if present
            moves = data.get("movements") or data.get("movement") or []
            if isinstance(moves, list):
                movements = moves
            elif moves:
                movements = [moves]
        if not isinstance(movements, list):
            movements = []

        return {
            "cocktail": cocktail_name_actual,
            "cocktail_id": cocktail_id,
            "movements": movements,
            "errors": []
        }

    async def get_inventory_items_by_ingredient(self, ingredient_id: str,
                                                 location: str = "BAR") -> List[Dict]:
        """Find inventory items for an ingredient."""
        all_items = await self.get_inventory_items(location=location)
        ingredient_id_str = str(ingredient_id)

        matching_items = []
        for item in all_items:
            # Direct match for garnish items
            if item.get("ingredient_id") == ingredient_id_str:
                matching_items.append(item)

        # If no direct matches, try to find via bottle relationship
        if not matching_items:
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(
                        f"{self.base_url}/ingredients/{ingredient_id}/bottles",
                        headers=self._headers()
                    )
                    if response.status_code == 200:
                        bottles = response.json()
                        bottle_ids = {str(b["id"]) for b in bottles}
                        matching_items = [
                            item for item in all_items
                            if item.get("bottle_id") in bottle_ids
                        ]
            except Exception as e:
                logger.warning(f"Could not check bottle relationship: {e}")

        return matching_items
