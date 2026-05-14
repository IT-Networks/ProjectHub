import { describe, it, expect } from 'vitest'
import { convertTaskListHTML } from './taskUtils'

describe('convertTaskListHTML', () => {
  it('rewrites a task-item <li> into a <label> structure', () => {
    const html = '<li class="is-task-item"><input type="checkbox" /><p>Buy milk</p></li>'
    const out = convertTaskListHTML(html)
    expect(out).toContain('<label data-task-item')
    expect(out).toContain('<span>Buy milk</span>')
    expect(out).not.toContain('<li')
  })

  it('preserves the checked state', () => {
    const checked = '<li class="is-task-item"><input type="checkbox" checked /><p>Done</p></li>'
    expect(convertTaskListHTML(checked)).toContain('checked')

    const unchecked = '<li class="is-task-item"><input type="checkbox" /><p>Open</p></li>'
    expect(convertTaskListHTML(unchecked)).not.toContain('checked')
  })

  it('converts multiple task items in one pass', () => {
    const html =
      '<li class="is-task-item"><input type="checkbox" /><p>One</p></li>' +
      '<li class="is-task-item"><input type="checkbox" checked /><p>Two</p></li>'
    const out = convertTaskListHTML(html)
    expect(out.match(/<label data-task-item/g)).toHaveLength(2)
    expect(out).toContain('<span>One</span>')
    expect(out).toContain('<span>Two</span>')
  })

  it('leaves non-task markup untouched', () => {
    const html = '<p>just a paragraph</p>'
    expect(convertTaskListHTML(html)).toBe(html)
  })
})
