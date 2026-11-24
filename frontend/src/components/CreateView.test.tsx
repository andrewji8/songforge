import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import CreateView from './CreateView'
import type { CreateJobInput } from '../types'

const originalNavigator = globalThis.navigator

beforeEach(() => {
  // 让麦克风相关调用快速失败，不影响文本/循环路径测试
  Object.defineProperty(globalThis, 'navigator', {
    value: {
      ...originalNavigator,
      mediaDevices: {
        getUserMedia: vi.fn().mockRejectedValue(new Error('no mic in jsdom')),
      },
    },
    configurable: true,
  })
})

afterEach(() => {
  Object.defineProperty(globalThis, 'navigator', {
    value: originalNavigator,
    configurable: true,
  })
  vi.unstubAllGlobals()
})

function renderCreateView(onSubmit: ReturnType<typeof vi.fn>) {
  return render(<CreateView onSubmit={onSubmit} />)
}

describe('CreateView text job', () => {
  it('submits a valid text job with default values', () => {
    const onSubmit = vi.fn()
    renderCreateView(onSubmit)

    const desc = screen.getByLabelText(/描述你想要的歌曲/)
    fireEvent.change(desc, { target: { value: '深夜雨声里想念旧朋友，LoFi 风格' } })
    fireEvent.click(screen.getByText('生成歌曲'))

    expect(onSubmit).toHaveBeenCalledTimes(1)
    expect(onSubmit).toHaveBeenCalledWith({
      kind: 'text',
      description: '深夜雨声里想念旧朋友，LoFi 风格',
      durationSecs: 190,
      language: 'zh',
      styleHints: [],
      lyrics: null,
    } as CreateJobInput)
  })

  it('rejects description shorter than 4 chars', () => {
    const onSubmit = vi.fn()
    renderCreateView(onSubmit)

    const desc = screen.getByLabelText(/描述你想要的歌曲/)
    fireEvent.change(desc, { target: { value: 'ab' } })
    fireEvent.click(screen.getByText('生成歌曲'))

    expect(onSubmit).not.toHaveBeenCalled()
    expect(screen.getByText(/请至少输入 4 个字的描述/)).toBeInTheDocument()
  })

  it('toggles style hint chips', () => {
    const onSubmit = vi.fn()
    renderCreateView(onSubmit)

    // 展开高级选项
    fireEvent.click(screen.getByText(/高级选项/))

    const popChip = screen.getByText('Pop')
    fireEvent.click(popChip)
    expect(popChip).toHaveClass('chip')
    expect(popChip).toHaveClass('on')

    // 再次点击取消
    fireEvent.click(popChip)
    expect(popChip).not.toHaveClass('on')
  })

  it('switches to loop tab and accepts a file', () => {
    const onSubmit = vi.fn()
    renderCreateView(onSubmit)

    fireEvent.click(screen.getByText('节拍 / Loop'))

    const fileInput = screen.getByLabelText(/上传一段节拍 \/ loop/)
    const file = new File(['dummy'], 'loop.wav', { type: 'audio/wav' })
    fireEvent.change(fileInput, { target: { files: [file] } })

    expect(screen.getByText('已选择：loop.wav')).toBeInTheDocument()

    const desc = screen.getByLabelText(/描述你想要的歌曲/)
    fireEvent.change(desc, { target: { value: 'loop test' } })
    fireEvent.click(screen.getByText('生成歌曲'))

    expect(onSubmit).toHaveBeenCalledTimes(1)
    const call = onSubmit.mock.calls[0][0] as CreateJobInput
    expect(call.kind).toBe('loop')
    expect(call.file).toBe(file)
    expect(call.description).toBe('loop test')
  })
})