/// Ordering rules for the Providers tab's reorderable list.
///
/// Kept apart from the widget so the drag can be tested as arithmetic.
library;

/// Move one entry of [orderedIds] the way `ReorderableListView` reports it.
///
/// [newIndex] is the insertion index *before* the moved item is removed, which
/// is why a downward move lands one place earlier.
List<String> moveEntry(List<String> orderedIds, int oldIndex, int newIndex) {
  if (oldIndex < 0 || oldIndex >= orderedIds.length) {
    return List<String>.of(orderedIds);
  }
  final result = List<String>.of(orderedIds);
  final moved = result.removeAt(oldIndex);
  final target = newIndex > oldIndex ? newIndex - 1 : newIndex;
  result.insert(target.clamp(0, result.length), moved);
  return result;
}

/// The enabled ids after a drag, in their new order.
///
/// Only enabled providers live in the stored list, so a drag over a disabled
/// row moves it past without adding it.
List<String> reorderEnabled(
  List<String> orderedIds,
  Set<String> enabled,
  int oldIndex,
  int newIndex,
) {
  return moveEntry(orderedIds, oldIndex, newIndex)
      .where(enabled.contains)
      .toList();
}
