import '@testing-library/jest-dom'

// 让 ResizeObserver / matchMedia 在 jsdom 中可用（部分组件若后续接入）
if (typeof window.ResizeObserver === 'undefined') {
  // eslint-disable-next-line @typescript-eslint/no-empty-object-type
  window.ResizeObserver = class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
}