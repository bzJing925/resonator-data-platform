import React, { createContext, useCallback, useContext, useMemo, useState } from 'react';

const UploadProgressContext = createContext(null);

export function UploadProgressProvider({ children }) {
  const [tasks, setTasks] = useState([]);

  const addTask = useCallback((task) => {
    setTasks((prev) => {
      if (prev.some((t) => t.task_id === task.task_id)) return prev;
      return [...prev, { ...task, addedAt: Date.now() }];
    });
  }, []);

  const removeTask = useCallback((taskId) => {
    setTasks((prev) => prev.filter((t) => t.task_id !== taskId));
  }, []);

  const updateTask = useCallback((taskId, updates) => {
    setTasks((prev) =>
      prev.map((t) => (t.task_id === taskId ? { ...t, ...updates } : t))
    );
  }, []);

  const value = useMemo(
    () => ({ tasks, addTask, removeTask, updateTask }),
    [tasks, addTask, removeTask, updateTask]
  );

  return (
    <UploadProgressContext.Provider value={value}>
      {children}
    </UploadProgressContext.Provider>
  );
}

export function useUploadProgress() {
  const ctx = useContext(UploadProgressContext);
  if (!ctx) {
    throw new Error('useUploadProgress must be used within UploadProgressProvider');
  }
  return ctx;
}
