import { categoryNoun } from '../format';
import { hoursStatus, parseHours, weekLines } from '../hours';

// 2026-11-11 is a Wednesday.
const at = (hh: number, mm = 0, day = 11) => new Date(2026, 10, day, hh, mm);

describe('opening hours', () => {
  it('reads 24/7', () => {
    expect(hoursStatus('24/7', at(3))).toEqual({ open: true, always: true, text: 'Open 24 hours' });
  });

  it('reads day ranges, later rules overriding earlier ones', () => {
    const week = parseHours('Mo-Sa 09:00-21:00; Sa 10:00-14:00; Su off')!;
    expect(week[0]).toEqual([[540, 1260]]);
    expect(week[5]).toEqual([[600, 840]]);
    expect(week[6]).toEqual([]);
  });

  it('gives a live status', () => {
    expect(hoursStatus('Mo-Fr 09:00-17:00', at(10))?.text).toBe('Open now · closes 5 PM');
    expect(hoursStatus('Mo-Fr 09:00-17:00', at(8, 30))?.text).toBe('Closed · opens 9 AM');
    expect(hoursStatus('Mo-Fr 09:00-17:00', at(18))?.text).toBe('Closed · opens tomorrow 9 AM');
    expect(hoursStatus('Mo-Fr 09:00-17:00', at(18, 0, 13))?.text).toBe('Closed · opens Mon 9 AM');   // Friday evening
  });

  it('handles split and overnight hours', () => {
    expect(hoursStatus('Mo-Su 09:00-13:00,14:00-18:30', at(13, 30))?.text).toBe('Closed · opens 2 PM');
    expect(hoursStatus('Mo-Su 18:00-02:00', at(1))?.text).toBe('Open now · closes 2 AM');            // after midnight
    expect(hoursStatus('Mo-Su 18:00-02:00', at(20))?.text).toBe('Open now · closes 2 AM');
  });

  it('returns null for forms it cannot read', () => {
    expect(parseHours('sunrise-sunset')).toBeNull();
    expect(parseHours('Jan-Mar Mo 09:00-12:00')).toBeNull();
    expect(hoursStatus(null)).toBeNull();
  });

  it('lists the week for display', () => {
    expect(weekLines('Mo-Fr 09:00-17:30; Sa,Su off')![0]).toEqual(['Monday', '9 AM – 5:30 PM']);
    expect(weekLines('Mo-Fr 09:00-17:30; Sa,Su off')![6]).toEqual(['Sunday', 'Closed']);
  });
});

describe('category nouns', () => {
  it('pluralizes labels for sentences', () => {
    expect(categoryNoun('Pharmacy')).toBe('pharmacies');
    expect(categoryNoun('Fuel station')).toBe('fuel stations');
    expect(categoryNoun('Bank')).toBe('banks');
    expect(categoryNoun('IT & software')).toBe('IT & software businesses');
    expect(categoryNoun('Manufacturing')).toBe('manufacturing businesses');
    expect(categoryNoun('Pharmacy', false)).toBe('pharmacy');
  });
});

describe('category nouns for real labels', () => {
  it('keeps every-day labels natural', () => {
    expect(categoryNoun('Post office')).toBe('post offices');
    expect(categoryNoun('Real estate')).toBe('real estate businesses');
    expect(categoryNoun('Education')).toBe('education businesses');
    expect(categoryNoun('Wedding & event venue')).toBe('wedding & event venue businesses');
    expect(categoryNoun('Dentist')).toBe('dentists');
  });
});
