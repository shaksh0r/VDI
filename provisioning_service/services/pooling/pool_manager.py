import uuid

vm_states = {'draft','provisioning','ready','assigned','in-use','error'}


class VM:
    def __init__(self,state='draft'):
        if state.lower() not in vm_states:
            raise ValueError("unknown vm state")
        self.state = state
        self.id = uuid.uuid4()


class Pool_Manager:
    def __init__(self, min_vm = 5):
        self.min_vm = min_vm
        self.vm_list = []
        self.vm_count = {"draft":0,"provisioning":0,"ready":0,"assigned":0,"in-use":"0","error":0}
    
    def add_vm(self,vm:VM):
        self.vm_list.append(vm)
        self.vm_count['provisioning'] += 1
    

    def remove_vm(self,id:str):
        for vm in self.vm_list:
            if str(vm.id) == id:
                self.vm_list.remove(vm)
                self.vm_count['ready'] -= 1
                return 0
        
        return -1
    

    def state_change(self, id:str, changed_state:str):
        vm = self.get_vm(id)
        if vm == -1:
            return -1
        
        if changed_state not in vm_states:
            return -1
        
        special_states = ('ready','assigned','in-use','error')

        if vm.state in special_states:
            if changed_state in special_states:
                self.vm_count[vm.state] -= 1
                vm.state = changed_state
                self.vm_count[vm.state] += 1
                return
            else:
                return -1
        else:
            self.vm_count[vm.state] -= 1
            vm.state = changed_state
            self.vm_count[vm.state] += 1
            return 0

    
    def pool_status(self):
        status = {}
        status_count = {"draft":0,"provisioning":0,"ready":0,"assigned":0,"in-use":"0","error":0}
        status["vm_count"] = len(self.vm_list)

        for vm in self.vm_list:
            match vm.state:
                case "draft":
                    status_count["draft"] += 1
                case "provisioning":
                    status_count["provisioning"] += 1
                case "ready":
                    status_count["ready"] += 1
                case "assigned":
                    status_count["assigned"] += 1
                case "in-use":
                    status_count["in-use"] += 1
                case "error":
                    status_count["error"] += 1

        
        status["status_count"] = status_count
    
    def get_vm(self,id:str):
        for vm in self.vm_list:
            if vm.id == id:
                return vm
        
        return -1

    def filter_vm(self,filter_state:str):
        if filter_state.lower() not in vm_states:
            return -1
        
        filtered_list = []

        for vm in self.vm_list:
            if vm.state == filter_state.lower():
                filtered_list.append(vm.id)
        

        return filtered_list
        

