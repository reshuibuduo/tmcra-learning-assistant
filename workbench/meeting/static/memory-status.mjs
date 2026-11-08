export function memoryBadge(health) {
  if (health?.memory_recall_available) return '学习关联已启用';
  return health?.tmcra_write ? '记忆写入已启用' : '无召回模式';
}
export function memoryNote(health) {
  const recall = !!health?.memory_recall_available;
  const write = !!health?.tmcra_write;
  if (recall && write) return '结合你导入的学习记录给出带出处的提示；本工作台的转写会写入独立记忆库。';
  if (recall) return '结合你导入的学习记录给出带出处的提示；课堂不自动写入记忆。';
  if (write) return '本工作台的转写会写入独立记忆库；导入学习记录后可建立知识关联。';
  return '概念解释独立运行；可手动导入以前的学习对话。';
}
