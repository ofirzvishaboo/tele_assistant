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
    async def reduce_stock(self, inventory_item_id: str, amount: float,
                          location: str, reason: str,
                          source_type: Optional[str] = None,
                          source_id: Optional[int] = None) -> Dict:
        """Reduce inventory stock (negative change)."""
        await self._ensure_authenticated()

        payload = {
            "location": location,
            "inventory_item_id": str(inventory_item_id),
            "change": -abs(amount),  # Ensure negative
            "reason": reason,
        }
        if source_type:
            payload["source_type"] = source_type
        if source_id:
            payload["source_id"] = source_id

        try:
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
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 403:
                logger.error("Insufficient permissions: Bot user must be superuser")
                raise ValueError("Bot user does not have permission to create inventory movements")
            logger.error(f"Failed to reduce stock: {e}")
            raise

    async def process_cocktail_order(self, cocktail_name: str, location: Optional[str] = None,
                                    event_id: Optional[int] = None) -> Dict:
        """
        Process a cocktail order by name: get ingredients and reduce stock.

        Args:
            cocktail_name: Name of the cocktail (will be resolved to ID)
            location: Location for inventory (defaults to configured location)
            event_id: Optional event ID for tracking

        Returns:
            Summary of movements created
        """
        if not location:
            location = self.location

        # Search for cocktail by name
        matches = await self.search_cocktail_by_name(cocktail_name)

        if not matches:
            raise ValueError(f"Cocktail '{cocktail_name}' not found")

        if len(matches) > 1:
            # Multiple matches - return them for user to choose
            raise ValueError(
                f"Multiple cocktails match '{cocktail_name}': {[m['name'] for m in matches]}"
            )

        cocktail = matches[0]
        cocktail_id = cocktail["id"]
        cocktail_name_actual = cocktail["name"]

        # Get full cocktail details
        cocktail_details = await self.get_cocktail(cocktail_id)

        movements = []
        errors = []

        # Process each ingredient
        for ingredient in cocktail_details.get("recipe_ingredients", []):
            ingredient_id = ingredient.get("ingredient_id")
            ingredient_name = ingredient.get("ingredient_name")
            quantity = ingredient.get("quantity")
            unit = ingredient.get("unit")

            if not ingredient_id:
                errors.append(f"Skipping {ingredient_name}: no ingredient_id")
                continue

            # Find inventory item for this ingredient
            inventory_items = await self.get_inventory_items_by_ingredient(
                ingredient_id, location
            )

            if not inventory_items:
                errors.append(f"No inventory item found for {ingredient_name}")
                continue

            # Use first matching item, or match by bottle_id if specified
            inventory_item = inventory_items[0]
            if ingredient.get("bottle_id"):
                matching_bottle = next(
                    (item for item in inventory_items
                     if item.get("bottle_id") == str(ingredient.get("bottle_id"))),
                    None
                )
                if matching_bottle:
                    inventory_item = matching_bottle

            inventory_item_id = inventory_item["id"]

            # Reduce stock
            try:
                movement = await self.reduce_stock(
                    inventory_item_id=inventory_item_id,
                    amount=quantity,
                    location=location,
                    reason=f"Cocktail served: {cocktail_name_actual}",
                    source_type="telegram_event",
                    source_id=event_id
                )
                movements.append({
                    "ingredient": ingredient_name,
                    "quantity": quantity,
                    "unit": unit,
                    "movement": movement
                })
            except Exception as e:
                errors.append(f"Failed to reduce stock for {ingredient_name}: {str(e)}")

        return {
            "cocktail": cocktail_name_actual,
            "cocktail_id": cocktail_id,
            "movements": movements,
            "errors": errors
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
