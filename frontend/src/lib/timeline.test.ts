import { describe, it, expect } from 'vitest'
import {
  startOfDay,
  addDays,
  isSameDay,
  isSameMonth,
  startOfISOWeek,
  endOfISOWeek,
  startOfMonth,
  endOfMonth,
} from './timeline'

describe('timeline date helpers', () => {
  describe('startOfDay', () => {
    it('zeroes the time component', () => {
      const d = startOfDay(new Date(2026, 4, 14, 17, 42, 30))
      expect(d).toEqual(new Date(2026, 4, 14))
      expect(d.getHours()).toBe(0)
    })
  })

  describe('addDays', () => {
    it('rolls forward across a month boundary', () => {
      expect(addDays(new Date(2026, 4, 30), 3)).toEqual(new Date(2026, 5, 2))
    })

    it('rolls backward with a negative offset', () => {
      expect(addDays(new Date(2026, 4, 1), -1)).toEqual(new Date(2026, 3, 30))
    })

    it('does not mutate its input', () => {
      const input = new Date(2026, 4, 14)
      addDays(input, 5)
      expect(input).toEqual(new Date(2026, 4, 14))
    })
  })

  describe('isSameDay', () => {
    it('ignores the time component', () => {
      expect(isSameDay(new Date(2026, 4, 14, 1), new Date(2026, 4, 14, 23))).toBe(true)
    })

    it('is false across a day boundary', () => {
      expect(isSameDay(new Date(2026, 4, 14), new Date(2026, 4, 15))).toBe(false)
    })
  })

  describe('isSameMonth', () => {
    it('matches dates within the same month', () => {
      expect(isSameMonth(new Date(2026, 4, 1), new Date(2026, 4, 31))).toBe(true)
    })

    it('is false across months or years', () => {
      expect(isSameMonth(new Date(2026, 4, 1), new Date(2026, 5, 1))).toBe(false)
      expect(isSameMonth(new Date(2026, 4, 1), new Date(2025, 4, 1))).toBe(false)
    })
  })

  describe('startOfISOWeek / endOfISOWeek', () => {
    // 2026-05-14 is a Thursday, 2026-05-17 is a Sunday.
    it('returns the preceding Monday for a mid-week date', () => {
      const mon = startOfISOWeek(new Date(2026, 4, 14))
      expect(mon.getDay()).toBe(1)
      expect(mon).toEqual(new Date(2026, 4, 11))
    })

    it('returns the preceding Monday for a Sunday (ISO weeks start Monday)', () => {
      expect(startOfISOWeek(new Date(2026, 4, 17))).toEqual(new Date(2026, 4, 11))
    })

    it('endOfISOWeek is the Sunday six days later', () => {
      const sun = endOfISOWeek(new Date(2026, 4, 14))
      expect(sun.getDay()).toBe(0)
      expect(sun).toEqual(new Date(2026, 4, 17))
    })
  })

  describe('startOfMonth / endOfMonth', () => {
    it('startOfMonth is the first of the month', () => {
      expect(startOfMonth(new Date(2026, 4, 14))).toEqual(new Date(2026, 4, 1))
    })

    it('endOfMonth is the last day of the month', () => {
      expect(endOfMonth(new Date(2026, 4, 14))).toEqual(new Date(2026, 4, 31))
    })

    it('endOfMonth handles February in a non-leap year', () => {
      expect(endOfMonth(new Date(2026, 1, 1))).toEqual(new Date(2026, 1, 28))
    })
  })
})
