import { Dispatch, SetStateAction, useEffect, useState } from 'react';

type PersistedState<T> = [T, Dispatch<SetStateAction<T>>];

function usePersistedState<T>(defaultValue: T, key: string): PersistedState<T> {
  const [value, setValue] = useState<T>(() => {
    const raw = window.localStorage.getItem(key);
    if (!raw) return defaultValue;

    try {
      const parsed = JSON.parse(raw) as T;
      if (
        typeof defaultValue === "object" &&
        defaultValue !== null &&
        !Array.isArray(defaultValue) &&
        typeof parsed === "object" &&
        parsed !== null &&
        !Array.isArray(parsed)
      ) {
        return {
          ...defaultValue,
          ...parsed,
        };
      }
      return parsed;
    } catch {
      return defaultValue;
    }
  });

  useEffect(() => {
    try {
      window.localStorage.setItem(key, JSON.stringify(value));
    } catch (e) {
      console.error(`Failed to persist key ${key}`, e);
    }
  }, [key, value]);

  return [value, setValue];
}

export { usePersistedState };
