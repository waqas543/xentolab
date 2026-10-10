"""Filters shared by every company endpoint, so the dashboard's filter bar scopes everything."""

from dataclasses import dataclass, field
from typing import Annotated

from fastapi import Query
from psycopg import sql

COMPANIES = sql.SQL("osm_app.companies")

# Columns the UI may group or pivot by.
DIMENSIONS = {
    "category_group", "category", "province", "district", "city", "locality", "brand", "osm_type",
}

CONTACT_FLAGS = ["has_phone", "has_website", "has_email", "has_address", "has_opening_hours"]


@dataclass
class CompanyFilters:
    q: str | None = None
    group: list[str] = field(default_factory=list)
    category: list[str] = field(default_factory=list)
    province: list[str] = field(default_factory=list)
    district: list[str] = field(default_factory=list)
    city: list[str] = field(default_factory=list)
    brand: list[str] = field(default_factory=list)
    flags: dict[str, bool] = field(default_factory=dict)
    min_completeness: int | None = None
    branded: bool | None = None

    def where(self, alias: str | None = None, extra: list[str] | None = None):
        """Return (WHERE clause, params). `extra` adds fixed SQL conditions.

        The clause is empty when there is nothing to filter on.
        """

        def col(name):
            return sql.Identifier(alias, name) if alias else sql.Identifier(name)

        clauses = [sql.SQL(condition) for condition in extra or []]
        params = {}

        if self.q:
            clauses.append(sql.SQL("({} ILIKE %(q)s OR {} ILIKE %(q)s OR {} ILIKE %(q)s)").format(
                col("name"), col("name_local"), col("brand"),
            ))
            params["q"] = f"%{self.q.strip()}%"

        for name, values in [
            ("category_group", self.group),
            ("category", self.category),
            ("province", self.province),
            ("district", self.district),
            ("city", self.city),
            ("brand", self.brand),
        ]:
            if values:
                clauses.append(sql.SQL("{} = ANY(%({})s)").format(col(name), sql.SQL(name)))
                params[name] = values

        for flag, wanted in self.flags.items():
            clauses.append(sql.SQL("{} = %({})s").format(col(flag), sql.SQL(flag)))
            params[flag] = wanted

        if self.min_completeness is not None:
            clauses.append(sql.SQL("{} >= %(min_completeness)s").format(col("completeness")))
            params["min_completeness"] = self.min_completeness

        if self.branded is not None:
            clauses.append(
                sql.SQL("{} IS NOT NULL").format(col("brand"))
                if self.branded
                else sql.SQL("{} IS NULL").format(col("brand"))
            )

        if not clauses:
            return sql.SQL(""), params

        return sql.SQL("WHERE ") + sql.SQL(" AND ").join(clauses), params


def company_filters(
    q: str | None = None,
    group: Annotated[list[str] | None, Query()] = None,
    category: Annotated[list[str] | None, Query()] = None,
    province: Annotated[list[str] | None, Query()] = None,
    district: Annotated[list[str] | None, Query()] = None,
    city: Annotated[list[str] | None, Query()] = None,
    brand: Annotated[list[str] | None, Query()] = None,
    has_phone: bool | None = None,
    has_website: bool | None = None,
    has_email: bool | None = None,
    has_address: bool | None = None,
    has_opening_hours: bool | None = None,
    min_completeness: Annotated[int | None, Query(ge=0, le=100)] = None,
    branded: bool | None = None,
) -> CompanyFilters:
    flags = {
        name: value
        for name, value in [
            ("has_phone", has_phone),
            ("has_website", has_website),
            ("has_email", has_email),
            ("has_address", has_address),
            ("has_opening_hours", has_opening_hours),
        ]
        if value is not None
    }

    return CompanyFilters(
        q=q or None,
        group=group or [],
        category=category or [],
        province=province or [],
        district=district or [],
        city=city or [],
        brand=brand or [],
        flags=flags,
        min_completeness=min_completeness,
        branded=branded,
    )
