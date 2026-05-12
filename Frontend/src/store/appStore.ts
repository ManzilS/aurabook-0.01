import { create } from 'zustand'
import type { ConnectionStatus } from '../types/app'

interface AppState {
  mobileMenuOpen: boolean
  connectionStatus: ConnectionStatus
  setMobileMenuOpen: (open: boolean) => void
  setConnectionStatus: (status: ConnectionStatus) => void
}

export const useAppStore = create<AppState>((set) => ({
  mobileMenuOpen: false,
  connectionStatus: 'standby',
  setMobileMenuOpen: (open) => set({ mobileMenuOpen: open }),
  setConnectionStatus: (status) => set({ connectionStatus: status }),
}))
