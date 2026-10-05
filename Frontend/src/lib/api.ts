import axios from 'axios'
import { appConfig } from './config'

export const apiClient = axios.create({
  baseURL: appConfig.apiBaseUrl,
  timeout: 10000,
})

apiClient.interceptors.response.use(
  (response) => response,
  (error: unknown) => {
    if (axios.isAxiosError(error)) {
      const detail = error.response?.data?.detail
      if (typeof detail === 'string') return Promise.reject(new Error(detail))
      if (!error.response) return Promise.reject(new Error('The API could not be reached or the request timed out. Check the backend connection.'))
    }
    return Promise.reject(error)
  },
)
