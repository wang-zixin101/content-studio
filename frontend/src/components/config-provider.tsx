import { createContext, useContext, useEffect, useState } from "react"

import { api } from "@/lib/api"
import type { AppConfig } from "@/lib/types"

interface ConfigState {
  config: AppConfig | null
  loading: boolean
  refresh: () => Promise<void>
}

const ConfigContext = createContext<ConfigState>({
  config: null,
  loading: true,
  refresh: async () => {},
})

export function ConfigProvider({ children }: { children: React.ReactNode }) {
  const [config, setConfig] = useState<AppConfig | null>(null)
  const [loading, setLoading] = useState(true)

  const refresh = async () => {
    try {
      setConfig(await api.getConfig())
    } catch {
      setConfig(null)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void refresh()
  }, [])

  return (
    <ConfigContext.Provider value={{ config, loading, refresh }}>
      {children}
    </ConfigContext.Provider>
  )
}

export function useConfig() {
  return useContext(ConfigContext)
}
