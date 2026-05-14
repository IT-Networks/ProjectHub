# Frontend Component Usage Guide
## New Components & Patterns (Phase 1-2)

**Version:** 1.0  
**Date:** 2026-04-19  
**Status:** Production-Ready ✅

---

## 📚 Table of Contents

1. [Design System Tokens](#design-system-tokens)
2. [EmptyState Component](#emptystate-component)
3. [Skeleton Loaders](#skeleton-loaders)
4. [Toast Notifications](#toast-notifications)
5. [Button Improvements](#button-improvements)
6. [Icon Updates](#icon-updates)
7. [Usage Examples](#usage-examples)
8. [Best Practices](#best-practices)

---

## Design System Tokens

**File:** `src/lib/design-system.ts`

### Spacing Scale (8px base)
```tsx
import { SPACING } from '@/lib/design-system'

// Available tokens
SPACING.none    // 0
SPACING.xs      // 4px   - tight spacing
SPACING.sm      // 8px   - small gaps
SPACING.md      // 16px  - default
SPACING.lg      // 24px  - large
SPACING.xl      // 32px  - extra large
SPACING['2xl']  // 48px  - page margins
```

**Usage:**
```tsx
<div className="p-[SPACING.md] gap-[SPACING.sm]">
  Content
</div>

// Or with style
<div style={{ padding: SPACING.lg, gap: SPACING.md }}>
  Content
</div>
```

### Typography Scale
```tsx
import { TYPOGRAPHY } from '@/lib/design-system'

// Available scales
TYPOGRAPHY.h1      // 32px, weight 700
TYPOGRAPHY.h2      // 24px, weight 700
TYPOGRAPHY.h3      // 18px, weight 600
TYPOGRAPHY.h4      // 16px, weight 600
TYPOGRAPHY.body    // 14px, weight 400
TYPOGRAPHY.caption // 12px, weight 500
TYPOGRAPHY.label   // 12px, weight 600
TYPOGRAPHY.code    // 13px, monospace
```

**Usage:**
```tsx
// In className
className={cn(
  'font-semibold',
  // Apply h1 size
  'text-2xl',  // 32px from Tailwind
)}

// Or manually
<div style={{
  fontSize: TYPOGRAPHY.h2.size,
  fontWeight: TYPOGRAPHY.h2.weight,
  lineHeight: TYPOGRAPHY.h2.lineHeight,
}}>
  Title
</div>
```

### Other Tokens
```tsx
import { RADIUS, SHADOWS, TRANSITIONS, Z_INDEX } from '@/lib/design-system'

// Border radius
RADIUS.sm       // 4px
RADIUS.md       // 8px
RADIUS.lg       // 12px
RADIUS.full     // 9999px

// Shadows (elevation levels)
SHADOWS.sm      // Subtle
SHADOWS.md      // Medium
SHADOWS.lg      // Large
SHADOWS.xl      // Extra large

// Transitions
TRANSITIONS.fast    // 150ms
TRANSITIONS.default // 200ms
TRANSITIONS.slow    // 300ms

// Z-Index
Z_INDEX.base       // 0
Z_INDEX.dropdown   // 100
Z_INDEX.modal      // 400
Z_INDEX.tooltip    // 600
```

---

## EmptyState Component

**File:** `src/components/shared/EmptyState.tsx`

### Basic Usage
```tsx
import { EmptyState } from '@/components/shared/EmptyState'

<EmptyState
  icon="📭"
  title="No items"
  description="Create your first item to get started"
  action={<Button onClick={handleCreate}>Create</Button>}
/>
```

### Props
```tsx
interface EmptyStateProps {
  icon?: ReactNode              // Emoji, icon, or component
  title: string                 // Main heading (required)
  description?: string          // Help text
  action?: ReactNode            // Button or custom action
  size?: 'compact' | 'normal' | 'spacious'  // Layout size
  className?: string            // Additional CSS
}
```

### Variants

**Compact** (for sidebars, small spaces):
```tsx
<EmptyStateCompact
  icon="📭"
  title="No items"
  action={<Button>Add</Button>}
/>
```

**Card** (with border, for emphasis):
```tsx
<EmptyStateCard
  icon="📁"
  title="No projects"
  description="Create your first project..."
  action={<Button>Create</Button>}
/>
```

### Real-World Examples

**Dashboard:**
```tsx
{widgets.length === 0 ? (
  <EmptyState
    icon="📊"
    title="No widgets configured"
    description="Add widgets to customize your dashboard"
    action={<Button onClick={addWidget}>Add Widget</Button>}
    size="spacious"
  />
) : (
  <WidgetGrid />
)}
```

**Project List:**
```tsx
{projects.length === 0 ? (
  <EmptyState
    icon="📁"
    title="No projects"
    description="Start by creating your first project"
    action={<Button onClick={createProject}>New Project</Button>}
  />
) : (
  <ProjectGrid projects={projects} />
)}
```

**Kanban Columns:**
```tsx
{todos.length === 0 ? (
  <EmptyStateCompact
    icon="📭"
    title="No todos"
  />
) : (
  <div>{/* todos */}</div>
)}
```

---

## Skeleton Loaders

**File:** `src/components/shared/Skeleton.tsx`

### Components

**Base Skeleton:**
```tsx
import { Skeleton } from '@/components/shared/Skeleton'

<Skeleton className="h-4 w-3/4" />
<Skeleton width={200} height={100} />
```

**Card Skeleton:**
```tsx
<CardSkeleton lines={3} />  // Default 3 lines

// Shows: header + 3 content lines + footer
```

**List Skeleton:**
```tsx
<ListSkeleton count={5} />  // Default 5 items
```

**Table Skeleton:**
```tsx
<TableSkeleton rows={10} columns={4} />
```

**Widget Skeleton:**
```tsx
<WidgetSkeleton />  // For dashboard widgets
```

**Avatar Skeleton:**
```tsx
<AvatarSkeleton size={40} />  // 40px avatar
```

**Full-screen Loader:**
```tsx
<ShimmerLoader message="Loading..." />
```

### Real-World Examples

**Dashboard Loading:**
```tsx
{loading && !widgets.length ? (
  <div className="grid grid-cols-4 gap-4">
    {Array.from({ length: 4 }).map((_, i) => (
      <WidgetSkeleton key={i} />
    ))}
  </div>
) : (
  <WidgetGrid />
)}
```

**Project List Loading:**
```tsx
{loading && !projects.length ? (
  <div className="grid grid-cols-3 gap-4">
    {Array.from({ length: 3 }).map((_, i) => (
      <CardSkeleton key={i} lines={2} />
    ))}
  </div>
) : (
  <ProjectList />
)}
```

**List Loading:**
```tsx
{emailLoading ? (
  <ListSkeleton count={3} />
) : emails.length === 0 ? (
  <EmptyState icon="📭" title="No emails" />
) : (
  <EmailList emails={emails} />
)}
```

---

## Toast Notifications

**File:** `src/components/shared/Toast.tsx`

### Using Hook (Recommended)

```tsx
import { useToast } from '@/components/shared/Toast'

export function MyComponent() {
  const { success, error, info, warning } = useToast()

  const handleSave = async () => {
    try {
      await saveData()
      success('Saved successfully!')
    } catch (err) {
      error('Failed to save', {
        description: err.message,
      })
    }
  }

  return <button onClick={handleSave}>Save</button>
}
```

### Hook API
```tsx
const {
  toasts,        // Array of current toasts
  addToast,      // (toast: ToastProps) => id
  removeToast,   // (id: string) => void
  success,       // (message, options) => id
  error,         // (message, options) => id
  info,          // (message, options) => id
  warning,       // (message, options) => id
} = useToast()
```

### With Action Button
```tsx
const { success } = useToast()

const handleDelete = async () => {
  await deleteItem()
  
  success('Item deleted', {
    action: {
      label: 'Undo',
      onClick: () => restoreItem(),
    },
    duration: 5000,
  })
}
```

### Direct Usage
```tsx
import { Toast } from '@/components/shared/Toast'

<Toast
  type="success"
  message="Saved!"
  duration={3000}
/>

<Toast
  type="error"
  message="Error"
  description="Something went wrong"
  action={{ label: 'Retry', onClick: handleRetry }}
/>
```

### Toast Types
```tsx
// Success (green with checkmark)
success('Operation completed successfully')

// Error (red with alert)
error('Failed to complete operation')

// Info (blue with info icon)
info('Your profile was updated')

// Warning (yellow with alert)
warning('This action cannot be undone')
```

---

## Button Improvements

### Icon + Label Pattern
```tsx
import { Plus, Edit, Trash } from 'lucide-react'
import { Button } from '@/components/ui/button'

// With icon
<Button icon={<Plus className="w-4 h-4" />}>
  Add Item
</Button>

// With icon position
<Button 
  icon={<Edit className="w-4 h-4" />}
  iconPosition="end"
>
  Edit
</Button>

// Icon only
<Button size="icon">
  <Plus className="w-4 h-4" />
</Button>
```

### Enhanced Affordance
```tsx
// All buttons now have:
// - Smooth transitions (200ms)
// - Hover effects (brightness/shadow)
// - Active state (scale-95 press animation)
// - Focus ring (accessible)

<Button>Default button</Button>
// Hover: brightens + shadow elevation
// Active: scales down to 95%
// Focus: ring outline
```

### Variants with Improved Feedback
```tsx
// Default (solid)
<Button variant="default">Primary</Button>
// Hover: brightness-110 + shadow elevation

// Outline (better border contrast)
<Button variant="outline">Secondary</Button>
// Hover: background + border color change

// Ghost (subtle)
<Button variant="ghost">Tertiary</Button>
// Hover: background highlight

// Destructive
<Button variant="destructive">Delete</Button>
// Hover: darker red background

// Link
<Button variant="link">Link text</Button>
// Hover: underline appears
```

---

## Icon Updates

### Available Icons (Lucide)
All icons are from `lucide-react` library (already imported).

**Common Icons Used:**
```tsx
import {
  Plus,
  Edit,
  Trash,
  X,
  Check,
  AlertCircle,
  Info,
  Mail,
  Link as LinkIcon,
  Search,
  Sun,
  Moon,
  LayoutGrid,
  Layers,
  Kanban,
  Calendar,
  Zap,
  Settings,
} from 'lucide-react'
```

### Sizing
```tsx
// Small (16px)
<Plus className="w-4 h-4" />

// Medium (20px)
<Plus className="w-5 h-5" />

// Large (24px)
<Plus className="w-6 h-6" />
```

### Button Usage
```tsx
<Button icon={<Plus className="w-4 h-4" />}>
  Add Item
</Button>
```

### Standalone Icons
```tsx
<div className="flex gap-2">
  <Mail className="w-5 h-5 text-blue-500" />
  <span>Email</span>
</div>
```

---

## Usage Examples

### Example 1: Dashboard with Loading & Empty State
```tsx
export function Dashboard() {
  const { widgets, loading, fetchDashboard } = useDashboardStore()

  useEffect(() => {
    fetchDashboard()
  }, [fetchDashboard])

  return (
    <div className="p-6">
      <h1 className="text-2xl font-bold mb-6">Dashboard</h1>

      {loading && !widgets.length ? (
        // Loading skeleton
        <div className="grid grid-cols-4 gap-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <WidgetSkeleton key={i} />
          ))}
        </div>
      ) : widgets.length === 0 ? (
        // Empty state
        <EmptyState
          icon="📊"
          title="No widgets yet"
          description="Customize your dashboard"
          action={<Button>Add Widget</Button>}
          size="spacious"
        />
      ) : (
        // Content
        <WidgetGrid />
      )}
    </div>
  )
}
```

### Example 2: Form with Toast Feedback
```tsx
export function CreateProjectForm() {
  const { success, error } = useToast()
  const createProject = useProjectStore(s => s.createProject)

  const handleSubmit = async (formData) => {
    try {
      await createProject(formData)
      success('Project created successfully!', {
        duration: 3000,
      })
      resetForm()
    } catch (err) {
      error('Failed to create project', {
        description: err.message,
        duration: 5000,
      })
    }
  }

  return (
    <form onSubmit={handleSubmit}>
      {/* form fields */}
      <Button type="submit">Create</Button>
    </form>
  )
}
```

### Example 3: List with Empty State & Skeleton
```tsx
export function EmailList() {
  const { emails, loading, searchEmails } = useInboxStore()

  return (
    <div>
      {loading ? (
        <ListSkeleton count={5} />
      ) : emails.length === 0 ? (
        <EmptyState
          icon="📭"
          title="No emails"
          description="Your inbox is empty"
        />
      ) : (
        <div className="space-y-2">
          {emails.map(email => (
            <EmailCard key={email.id} email={email} />
          ))}
        </div>
      )}
    </div>
  )
}
```

---

## Best Practices

### 1. Always Provide Loading States
```tsx
// ✅ Good
{loading ? <Skeleton /> : <Content />}

// ❌ Bad
{loading && <div>Loading...</div>}
```

### 2. Always Provide Empty States
```tsx
// ✅ Good
{items.length === 0 ? <EmptyState /> : <List />}

// ❌ Bad
{items.length === 0 && <p>No items</p>}
```

### 3. Use Consistent Icons
```tsx
// ✅ Good - same icon in multiple places
<Button icon={<Plus className="w-4 h-4" />}>
<Button icon={<Plus className="w-4 h-4" />}>

// ❌ Bad - inconsistent icons
<Button icon={<Plus className="w-5 h-5" />}>
<Button icon={<Add className="w-4 h-4" />}>
```

### 4. Use Toast for Feedback
```tsx
// ✅ Good
const { success } = useToast()
handleDelete()
success('Deleted!')

// ❌ Bad
handleDelete()
// No user feedback
```

### 5. Follow Design System
```tsx
// ✅ Good
import { SPACING, TYPOGRAPHY } from '@/lib/design-system'
<div style={{ padding: SPACING.lg }}>

// ❌ Bad
<div style={{ padding: '24px' }}>
```

### 6. Accessible Color Usage
```tsx
// ✅ Good - icon + color
<Button 
  icon={<Check className="text-green-600" />}
>
  Success

// ❌ Bad - color only
<div className="bg-green-500">Success</div>
```

---

## Integration Checklist

When building new features:

- [ ] Loading state with skeleton?
- [ ] Empty state with message + CTA?
- [ ] Icons where appropriate?
- [ ] Toast feedback for actions?
- [ ] Design system tokens used?
- [ ] Accessible markup?
- [ ] Dark mode tested?
- [ ] Mobile responsive?

---

## Common Patterns

### Pattern 1: List with all states
```tsx
{isLoading && <ListSkeleton count={5} />}
{!isLoading && items.length === 0 && <EmptyState />}
{!isLoading && items.length > 0 && <List items={items} />}
```

### Pattern 2: Create with feedback
```tsx
const handleCreate = async () => {
  try {
    await create(formData)
    success('Created!')
    closeDialog()
  } catch {
    error('Failed to create')
  }
}
```

### Pattern 3: Delete with undo
```tsx
const handleDelete = async () => {
  await delete(id)
  success('Deleted', {
    action: { label: 'Undo', onClick: restore },
  })
}
```

---

## Troubleshooting

### Toast not showing?
- Ensure you're using the hook: `const { success } = useToast()`
- Check that Toast component isn't being used directly in app
- Verify toast container is in your layout

### Skeleton looks wrong?
- Check grid/flex layout matches content
- Adjust `lines` prop for ListSkeleton
- Use specific height/width props if needed

### EmptyState not centered?
- Use within a centered container
- Or use `size="spacious"` for more padding
- Check parent container padding

### Icons not showing?
- Verify import from 'lucide-react'
- Check `className="w-4 h-4"` for sizing
- Ensure Lucide package is installed

---

## Performance Tips

1. **Memoize expensive renders:**
   ```tsx
   const visibleItems = useMemo(() => 
     items.filter(...)
   , [items])
   ```

2. **Use lazy loading for lists:**
   ```tsx
   {items.slice(0, 20).map(...)}
   {hasMore && <LoadMore />}
   ```

3. **Defer non-critical updates:**
   ```tsx
   useTransition(() => {
     setFiltered(...)
   })
   ```

---

**Last Updated:** 2026-04-19  
**Version:** 1.0  
**Status:** Production ✅

