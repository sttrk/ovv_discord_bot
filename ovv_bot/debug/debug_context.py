class DebugContext:
    def __init__(self):
        self.pg_conn = None
        self.notion = None
        self.openai_client = None

        # memory functions
        self.load_mem = None
        self.save_mem = None
        self.append_mem = None

        # brain functions
        self.brain_gen = None
        self.brain_load = None
        self.brain_save = None

        # core / llm system
        self.ovv_core = None
        self.ovv_external = None
        self.system_prompt = None


debug_context = DebugContext()
