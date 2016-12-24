from IPython.core.display import display

from bonobo.ext.jupyter.widget import BonoboWidget


class JupyterOutputPlugin:
    def initialize(self, context):
        self.widget = BonoboWidget()
        display(self.widget)

    def run(self, context):
        self.widget.value = [repr(component) for component in context.parent.components]

    finalize = run
