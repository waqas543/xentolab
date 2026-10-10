import type { City, Company, Place, Website } from '../lib/api';
import { distance, hostname, placeLine, plural, places } from '../lib/format';
import { useNav } from '../navigation/types';
import { GroupIcon, IconCircle, Row } from './ui';

export function PlaceRow({ place, last }: { place: Place; last?: boolean }) {
  const nav = useNav();
  const contact = [place.phone && 'Phone', place.website && 'Website'].filter(Boolean).join(' · ');
  return (
    <Row
      icon={<GroupIcon group={place.group} />}
      title={place.name}
      subtitle={[place.category, placeLine(place)].filter(Boolean).join(' · ')}
      detail={contact ? `Has ${contact.toLowerCase()}` : null}
      trailing={distance(place.distance_km)}
      onPress={() => nav.push('Place', { id: place.id })}
      last={last}
    />
  );
}

export function CompanyRow({ company, last }: { company: Company; last?: boolean }) {
  const nav = useNav();
  return (
    <Row
      icon={<GroupIcon group={company.group} />}
      title={company.name}
      subtitle={company.industry ?? company.group}
      detail={company.locations > 0 ? `${plural(company.locations, 'location')} in ${plural(company.cities, 'city', 'cities')}` : null}
      onPress={() => nav.push('Company', { id: company.id })}
      last={last}
    />
  );
}

export function CityRow({ city, last }: { city: City; last?: boolean }) {
  const nav = useNav();
  return (
    <Row
      icon={<IconCircle name="location" />}
      title={city.name}
      subtitle={[city.kind, city.province].filter(Boolean).join(' · ')}
      trailing={places(city.businesses)}
      onPress={() => nav.push('City', { id: city.id })}
      last={last}
    />
  );
}

export function WebsiteRow({ site, last }: { site: Website; last?: boolean }) {
  const nav = useNav();
  return (
    <Row
      icon={<IconCircle name="globe-outline" />}
      title={hostname(site.url) ?? site.domain}
      subtitle={[site.type_label, site.organization?.name].filter(Boolean).join(' · ')}
      trailing={site.businesses > 0 ? places(site.businesses) : null}
      onPress={() => nav.push('Website', { domain: site.domain })}
      last={last}
    />
  );
}
