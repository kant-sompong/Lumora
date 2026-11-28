import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../data/episode_calendar.dart';
import '../../data/settings_store.dart';
import '../../theme/lumora_theme.dart';
import '../media/future_guard.dart';

/// Upcoming episodes of any series, searchable by title.
///
/// Anime come from AniList (exact air times) and TV from TMDB (a day, no
/// time). A date the provider does not know stays "Air date unknown" unless a
/// regular cadence earns a projection, and every projected row says Estimated.
class CalendarPage extends StatefulWidget {
  const CalendarPage({super.key, this.search});

  /// Runs a search; tests pass a stub instead of the network service.
  final EpisodeSearch? search;

  @override
  State<CalendarPage> createState() => _CalendarPageState();
}

class _CalendarPageState extends State<CalendarPage> {
  final TextEditingController _controller = TextEditingController();
  Future<EpisodeCalendarResult>? _future;

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  void _run([String? value]) {
    final text = (value ?? _controller.text).trim();
    setState(() {
      _future = text.isEmpty ? null : _load(text);
    });
  }

  Future<EpisodeCalendarResult> _load(String query) {
    final injected = widget.search;
    if (injected != null) {
      return guarded(injected(query));
    }
    final service = EpisodeCalendarService(
      settings: context.read<AppSettings>(),
    );
    return guarded(service.search(query).whenComplete(service.dispose));
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Episode calendar')),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(
              LumoraSpace.lg,
              LumoraSpace.md,
              LumoraSpace.lg,
              LumoraSpace.sm,
            ),
            child: TextField(
              controller: _controller,
              textInputAction: TextInputAction.search,
              onSubmitted: _run,
              decoration: InputDecoration(
                hintText: 'Search a series for its next episodes',
                prefixIcon: const Icon(Icons.search),
                suffixIcon: IconButton(
                  tooltip: 'Clear',
                  icon: const Icon(Icons.close),
                  onPressed: () {
                    _controller.clear();
                    _run('');
                  },
                ),
                border: const OutlineInputBorder(),
              ),
            ),
          ),
          Expanded(child: _results(context)),
        ],
      ),
    );
  }

  Widget _results(BuildContext context) {
    final future = _future;
    if (future == null) {
      return ListView(
        padding: const EdgeInsets.all(LumoraSpace.xl),
        children: const [
          _Hint(
            text: 'Search for a series to see the episodes that air next. '
                'Anime times are exact; TV dates are the day the provider '
                'lists, and estimated dates are marked as such.',
          ),
        ],
      );
    }
    return FutureBuilder<EpisodeCalendarResult>(
      future: future,
      builder: (context, snapshot) {
        if (snapshot.connectionState == ConnectionState.waiting) {
          return const Center(
            child: Padding(
              padding: EdgeInsets.all(LumoraSpace.xl),
              child: Text('Searching…'),
            ),
          );
        }
        if (snapshot.hasError) {
          return ListView(
            padding: const EdgeInsets.all(LumoraSpace.lg),
            children: [_Hint(text: 'Could not search: ${snapshot.error}')],
          );
        }
        final result = snapshot.data;
        if (result == null || result.isEmpty) {
          final notes = <String>[
            ...?result?.errors,
            if (result != null && result.query.isNotEmpty)
              'No upcoming episodes found for “${result.query}”.',
          ];
          return ListView(
            padding: const EdgeInsets.all(LumoraSpace.xl),
            children: [
              for (final note in notes) _Hint(text: note),
            ],
          );
        }
        return _ResultList(result: result);
      },
    );
  }
}

class _ResultList extends StatelessWidget {
  const _ResultList({required this.result});

  final EpisodeCalendarResult result;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return ListView(
      padding: const EdgeInsets.fromLTRB(
        LumoraSpace.lg,
        0,
        LumoraSpace.lg,
        LumoraSpace.xl,
      ),
      children: [
        for (final error in result.errors)
          Padding(
            padding: const EdgeInsets.only(bottom: LumoraSpace.sm),
            child: Text(
              error,
              style: theme.textTheme.bodySmall?.copyWith(
                color: theme.colorScheme.error,
              ),
            ),
          ),
        for (final group in result.groups) ..._group(context, group),
      ],
    );
  }

  List<Widget> _group(BuildContext context, CalendarSeries group) {
    final theme = Theme.of(context);
    return <Widget>[
      Padding(
        padding: const EdgeInsets.only(top: LumoraSpace.lg, bottom: LumoraSpace.sm),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.end,
          children: [
            Expanded(
              child: Text(
                group.title,
                style: theme.textTheme.titleMedium
                    ?.copyWith(fontWeight: FontWeight.w700),
              ),
            ),
            Text(
              group.source,
              style: theme.textTheme.labelSmall?.copyWith(
                color: theme.colorScheme.onSurfaceVariant,
              ),
            ),
          ],
        ),
      ),
      for (final airing in group.airings) _AiringRow(airing: airing),
    ];
  }
}

class _AiringRow extends StatelessWidget {
  const _AiringRow({required this.airing});

  final EpisodeAiring airing;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final label = airing.label;
    return Padding(
      padding: const EdgeInsets.only(bottom: 6),
      child: Container(
        padding: const EdgeInsets.symmetric(
          horizontal: LumoraSpace.md,
          vertical: LumoraSpace.sm,
        ),
        decoration: BoxDecoration(
          color: theme.colorScheme.surfaceContainerHighest
              .withValues(alpha: 0.45),
          borderRadius: BorderRadius.circular(LumoraRadius.md),
        ),
        child: Row(
          children: [
            if (label.isNotEmpty) ...[
              SizedBox(
                width: 54,
                child: Text(
                  label,
                  style: theme.textTheme.bodyMedium?.copyWith(
                    color: theme.colorScheme.onSurfaceVariant,
                  ),
                ),
              ),
            ],
            Expanded(
              child: Text(
                airingDateLabel(airing),
                style: theme.textTheme.bodyMedium,
              ),
            ),
            if (airing.status.isNotEmpty)
              _StatusBadge(
                text: airing.status,
                estimated: airing.estimated,
              ),
          ],
        ),
      ),
    );
  }
}

class _StatusBadge extends StatelessWidget {
  const _StatusBadge({required this.text, required this.estimated});

  final String text;
  final bool estimated;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final background = estimated
        ? theme.colorScheme.tertiaryContainer
        : theme.colorScheme.secondaryContainer;
    final foreground = estimated
        ? theme.colorScheme.onTertiaryContainer
        : theme.colorScheme.onSecondaryContainer;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
      decoration: BoxDecoration(
        color: background,
        borderRadius: BorderRadius.circular(999),
      ),
      child: Text(
        text,
        style: theme.textTheme.labelSmall?.copyWith(color: foreground),
      ),
    );
  }
}

class _Hint extends StatelessWidget {
  const _Hint({required this.text});

  final String text;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.only(bottom: LumoraSpace.md),
      child: Text(
        text,
        style: theme.textTheme.bodyMedium?.copyWith(
          color: theme.colorScheme.onSurfaceVariant,
        ),
      ),
    );
  }
}
