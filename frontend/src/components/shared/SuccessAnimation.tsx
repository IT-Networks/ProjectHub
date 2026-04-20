import { useEffect, useState } from 'react'
import { Check } from 'lucide-react'
import { cn } from '@/lib/utils'

interface SuccessAnimationProps {
  show: boolean
  type?: 'checkmark' | 'confetti' | 'pulse'
  duration?: number
  onComplete?: () => void
}

export function SuccessAnimation({
  show,
  type = 'checkmark',
  duration = 1500,
  onComplete,
}: SuccessAnimationProps) {
  const [isVisible, setIsVisible] = useState(show)

  useEffect(() => {
    if (show) {
      setIsVisible(true)
      const timer = setTimeout(() => {
        setIsVisible(false)
        onComplete?.()
      }, duration)
      return () => clearTimeout(timer)
    }
  }, [show, duration, onComplete])

  if (!isVisible) return null

  if (type === 'checkmark') {
    return (
      <div className="fixed inset-0 pointer-events-none flex items-center justify-center">
        <div
          className={cn(
            'relative flex items-center justify-center',
            'animate-in zoom-in duration-300'
          )}
        >
          <div className="absolute inset-0 rounded-full bg-green-500/20 animate-out scale-125 opacity-0" />
          <div className="relative flex items-center justify-center w-24 h-24 rounded-full bg-green-500/10 border-2 border-green-500">
            <Check className="w-12 h-12 text-green-500 animate-in zoom-in-50 duration-500" />
          </div>
        </div>
      </div>
    )
  }

  if (type === 'confetti') {
    return (
      <div className="fixed inset-0 pointer-events-none overflow-hidden">
        {Array.from({ length: 30 }).map((_, i) => (
          <div
            key={i}
            className="absolute w-2 h-2 rounded-full animate-out fade-out slide-out-to-bottom"
            style={{
              left: `${Math.random() * 100}%`,
              top: '-10px',
              backgroundColor: ['#10b981', '#34d399', '#6ee7b7'][
                Math.floor(Math.random() * 3)
              ],
              animation: `fall ${2 + Math.random()}s linear forwards`,
              animationDelay: `${Math.random() * 0.2}s`,
            }}
          />
        ))}
        <style>{`
          @keyframes fall {
            to {
              transform: translateY(100vh) rotate(360deg);
              opacity: 0;
            }
          }
        `}</style>
      </div>
    )
  }

  return (
    <div className="fixed inset-0 pointer-events-none flex items-center justify-center">
      <div
        className={cn(
          'w-16 h-16 rounded-full bg-green-500',
          'animate-pulse'
        )}
      />
    </div>
  )
}

export function useSuccessAnimation() {
  const [showAnimation, setShowAnimation] = useState(false)
  const [animationType, setAnimationType] = useState<'checkmark' | 'confetti' | 'pulse'>('checkmark')

  const trigger = (type: 'checkmark' | 'confetti' | 'pulse' = 'checkmark') => {
    setAnimationType(type)
    setShowAnimation(true)
  }

  const reset = () => {
    setShowAnimation(false)
  }

  return {
    showAnimation,
    animationType,
    trigger,
    reset,
  }
}
