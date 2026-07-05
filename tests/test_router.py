from server.router import ConversationRouter
from server.schemas import ChatMessage

def test_router_continuation_and_branching():
    router = ConversationRouter()
    
    # Mock account creation in pool
    from server.accounts import pool, AccountInstance, AccountConfig
    pool.instances["sk-test"] = AccountInstance(AccountConfig(api_key="sk-test", session_dir="test_session"))

    # Turn 1: User asks "Hi"
    messages_turn_1 = [ChatMessage(role="user", content="Hi")]
    
    account, cid1, prompt1, head1 = router.route("sk-test", messages_turn_1, client_provided_cid=None)
    assert cid1 is None  # Should start a new conversation
    assert prompt1 == "Hi"
    
    # Simulate success: Copilot created conversation "conv-1" and responded "Hello! How can I help?"
    messages_turn_1_updated = messages_turn_1 + [ChatMessage(role="assistant", content="Hello! How can I help?")]
    head1 = router._hash_messages(messages_turn_1_updated)
    router.save_state(head1, "conv-1")
    
    # Turn 2: User asks "What is your name?"
    messages_turn_2 = messages_turn_1_updated + [ChatMessage(role="user", content="What is your name?")]
    
    account, cid2, prompt2, head2 = router.route("sk-test", messages_turn_2, client_provided_cid=None)
    assert cid2 == "conv-1"  # Should reuse!
    assert prompt2 == "What is your name?"
    
    # Simulate success: Copilot replied "I am Copilot"
    messages_turn_2_updated = messages_turn_2 + [ChatMessage(role="assistant", content="I am Copilot")]
    head2 = router._hash_messages(messages_turn_2_updated)
    router.save_state(head2, "conv-1")
    
    # Turn 3 (Branching): User edits their first message to "Hello Copilot!"
    # The client sends:
    messages_branch = [ChatMessage(role="user", content="Hello Copilot!")]
    
    account, cid_branch, prompt_branch, head_branch = router.route("sk-test", messages_branch, client_provided_cid=None)
    assert cid_branch is None  # Prefix hash doesn't match current head! Must start new!
    
    # Turn 4 (Re-roll): User re-rolls Turn 2
    # Client sends the exact same prompt as Turn 2
    account, cid_reroll, prompt_reroll, head_reroll = router.route("sk-test", messages_turn_2, client_provided_cid=None)
    assert cid_reroll is None  # Prefix hash of [Hi, Hello] matches OLD head, but NOT current head (head2). Must start new!
    
    # Turn 5 (Client explicit CID)
    messages_explicit = [ChatMessage(role="user", content="Test")]
    account, cid_exp, prompt_exp, head_exp = router.route("sk-test", messages_explicit, client_provided_cid="conv-explicit")
    assert cid_exp == "conv-explicit"
    
    print("All router tests passed!")

if __name__ == "__main__":
    test_router_continuation_and_branching()
