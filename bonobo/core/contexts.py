import traceback
import types
from functools import partial
from queue import Empty
from time import sleep

from bonobo.core.errors import InactiveReadableError
from bonobo.core.inputs import Input
from bonobo.core.stats import WithStatistics
from bonobo.util.lifecycle import get_initializer, get_finalizer
from bonobo.util.time import Timer
from bonobo.util.tokens import BEGIN, END, NEW, RUNNING, TERMINATED


class ExecutionContext:
    def __init__(self, graph, plugins=None):
        self.graph = graph
        self.components = [ComponentExecutionContext(component, self) for component in self.graph.components]

        self.plugins = [PluginExecutionContext(plugin, parent=self) for plugin in plugins or ()]

        for i, component_context in enumerate(self):
            try:
                component_context.outputs = [self[j].input for j in self.graph.outputs_of(i)]
            except KeyError as e:
                continue
            component_context.input.on_begin = partial(component_context.send, BEGIN, _control=True)
            component_context.input.on_end = partial(component_context.send, END, _control=True)

    def __getitem__(self, item):
        return self.components[item]

    def __len__(self):
        return len(self.components)

    def __iter__(self):
        yield from self.components

    @property
    def running(self):
        return any(component.running for component in self.components)


class PluginExecutionContext:
    def __init__(self, plugin, parent):
        self.parent = parent
        self.plugin = plugin
        self.alive = True

    def run(self):
        try:
            get_initializer(self.plugin)(self)
        except Exception as e:
            print('error in initializer', type(e), e)

        while self.alive:
            # todo with wrap_errors ....

            try:
                self.plugin.run(self)
            except Exception as e:
                print('error', type(e), e)

            sleep(0.25)

        try:
            get_finalizer(self.plugin)(self)
        except Exception as e:
            print('error in finalizer', type(e), e)

    def shutdown(self):
        self.alive = False


class ComponentExecutionContext(WithStatistics):
    """
    todo: make the counter dependant of parent context?
    """

    @property
    def name(self):
        return self.component.__name__

    @property
    def running(self):
        return self.input.alive

    def __init__(self, component, parent):
        self.parent = parent
        self.component = component
        self.input = Input()
        self.outputs = []
        self.state = NEW
        self.stats = {
            'in': 0,
            'out': 0,
            'err': 0,
            'read': 0,
            'write': 0,
        }

    def __repr__(self):
        """Adds "alive" information to the transform representation."""
        return ('+' if self.running else '-') + ' ' + self.name + ' ' + self.get_stats_as_string()

    def get_stats(self, *args, **kwargs):
        return (
            ('in', self.stats['in'],),
            ('out', self.stats['out'],),
            ('err', self.stats['err'],),
        )

    def impulse(self):
        self.input.put(None)

    def send(self, value, _control=False):
        if not _control:
            self.stats['out'] += 1
        for output in self.outputs:
            output.put(value)

    def recv(self, value):
        self.input.put(value)

    def get(self):
        row = self.input.get(timeout=1)
        return row

    def _call(self, row):
        # timer = Timer()
        # with timer:

        args = () if row is None else (row,)
        if getattr(self.component, '_with_context', False):
            return self.component(self, *args)
        return self.component(*args)

    def step(self, finalize=False):
        # Pull data from the first available input channel.

        """Runs a transformation callable with given args/kwargs and flush the result into the right
        output channel."""

        row = self.get()
        self.stats['in'] += 1

        results = self._call(row)

        # self._exec_time += timer.duration
        # Put data onto output channels
        if isinstance(results, types.GeneratorType):
            while True:
                # timer = Timer()
                # with timer:
                # todo _next ?
                try:
                    result = next(results)
                except StopIteration as e:
                    break
                # self._exec_time += timer.duration
                # self._exec_count += 1
                self.send(result)
        elif results is not None:
            # self._exec_count += 1
            self.send(results)
        else:
            pass
            # self._exec_count += 1

    def run(self):
        assert self.state is NEW, ('A {} can only be run once, and thus is expected to be in {} state at the '
                                   'beginning of a run().').format(type(self).__name__, NEW)

        self.state = RUNNING
        try:
            get_initializer(self.component)(self)
        except Exception as e:
            self.handle_error(e, traceback.format_exc())

        while True:
            try:
                self.step()
            except KeyboardInterrupt as e:
                raise
            except InactiveReadableError as e:
                sleep(1)
                # Terminated, exit loop.
                break  # BREAK !!!
            except Empty as e:
                continue
            except Exception as e:
                self.handle_error(e, traceback.format_exc())

        assert self.state is RUNNING, ('A {} must be in {} state when finalization starts.').format(
            type(self).__name__, RUNNING)

        self.state = TERMINATED
        try:
            get_finalizer(self.component)(self)
        except Exception as e:
            self.handle_error(e, traceback.format_exc())

    def handle_error(self, exc, tb):
        self.stats['err'] += 1
        print('\U0001F4A3 {} in {}'.format(type(exc).__name__, self.component))
        print(tb)
