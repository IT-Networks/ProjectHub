/**
 * Utility functions for task list handling
 */

export function convertTaskListHTML(html: string): string {
  // Konvertiert TipTap Task-List HTML zu Label-basierter Struktur
  // Input:  <li class="is-task-item"><input type="checkbox".../><p>text</p></li>
  // Output: <label data-task-item><input type="checkbox".../><span>text</span></label>

  return html.replace(
    /<li[^>]*class="[^"]*is-task-item[^"]*"[^>]*>(.*?)<\/li>/gi,
    (match, content) => {
      // Checkbox extrahieren
      const checkboxMatch = content.match(/<input[^>]*type="checkbox"[^>]*>/i);
      const checkbox = checkboxMatch ? checkboxMatch[0] : '<input type="checkbox" />';

      // Checked-Status extrahieren
      const isChecked = checkbox.includes('checked') ? 'checked' : '';

      // Text-Content aus <p> tags extrahieren
      const textMatch = content.match(/<p[^>]*>(.*?)<\/p>/i);
      const textContent = textMatch ? textMatch[1].trim() : content.replace(/<[^>]*>/g, '').trim();

      // Label-Struktur zurückgeben
      return `<label data-task-item class="flex items-center gap-2 cursor-pointer my-1"><input type="checkbox" class="w-4 h-4 shrink-0" ${isChecked} /><span>${textContent}</span></label>`;
    }
  );
}
